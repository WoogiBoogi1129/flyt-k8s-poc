#!/usr/bin/env python3
"""Boot a new SHM VM, provision fixed Python/CUDA files, run and collect D3.

The VM must have a single session and enough root disk space (16 GiB image).
The runner drains its own allocation even on failure. It never binds a GPU to
VFIO or reuses a released session. No CPU fallback or formal performance claim.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--name',required=True);p.add_argument('--key',required=True,type=Path)
p.add_argument('--bundle',required=True,type=Path,help='tar.gz with opt/torch-venv and opt/flyt-cuda-libs')
p.add_argument('--artifacts',required=True,type=Path,help='libflyt_guest.so, libnuma.so.1, libgomp.so.1')
p.add_argument('--fixture',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
p.add_argument('--barrier',type=Path);p.add_argument('--timeout',type=int,default=600)
a=p.parse_args()
if not re.fullmatch(r'evidence-[a-z0-9-]+',a.name):p.error('evidence VM name required')
a.output.mkdir(parents=True,exist_ok=False);ns='flyt-evidence';channel=a.name+'-channel'
remote='/opt/flyt-evidence';start=time.monotonic();timeline=[];process=None
def save(name,value):(a.output/name).write_text(json.dumps(value,indent=2))
def event(state,**values):
    timeline.append({'state':state,'seconds':time.monotonic()-start,**values});save('timeline.json',timeline)
    print(json.dumps(timeline[-1]),flush=True)
def k(*args):return subprocess.check_output(['kubectl',*args],text=True,timeout=30)
def get(kind,name):
    data=k('get',kind,name,'-n',ns,'--ignore-not-found','-o','json')
    return json.loads(data) if data.strip() else None
def wait(fn,label,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(2)
    raise TimeoutError(label)
initial=get('flytsharedmemorychannel',channel)
if not initial or initial['status']['phase']!='BackingReady' or initial['spec']['sessions']!=1:
    raise ValueError('fresh BackingReady single-session allocation required')
save('channel-before.json',initial)
files=[a.bundle,a.fixture,a.artifacts/'libflyt_guest.so',a.artifacts/'libnuma.so.1',a.artifacts/'libgomp.so.1',
       ROOT/'experiments/evidence/train.py',ROOT/'experiments/evidence/guest_training_suite.py',Path(__file__)]
save('manifest.json',{'phase':'development','formal_result':False,'name':a.name,
    'channel_uid':initial['metadata']['uid'],'channel_spec':initial['spec'],
    'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'sha256':{str(f):hashlib.file_digest(f.open('rb'),'sha256').hexdigest() for f in files}})
metrics={'status':'FAIL','formal_result':False}
try:
    k('patch','vm',a.name,'-n',ns,'--type=merge','-p','{"spec":{"runStrategy":"Always"}}');event('StartRequested')
    def running():
        v=get('vmi',a.name)
        return v if v and v.get('status',{}).get('phase')=='Running' and v['status'].get('interfaces') else None
    v=wait(running,'VMI Running',300);save('vmi-running.json',v)
    ip=str(ipaddress.ip_address(v['status']['interfaces'][0]['ipAddress']))
    options=['-i',str(a.key.resolve()),'-o','BatchMode=yes','-o','ConnectTimeout=5',
             '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str((a.output/'known-hosts').resolve())]
    ssh=['ssh',*options,'ubuntu@'+ip];scp=['scp',*options]
    def command(command_text,**kwargs):return subprocess.run(ssh+[command_text],**kwargs)
    wait(lambda:command('true',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0,'SSH ready',120)
    event('SSHReady')
    inventory=command('uname -r; python3 --version; df -B1 /; ls /dev/nvidia* 2>/dev/null || true',capture_output=True,text=True,timeout=15)
    (a.output/'guest-environment.txt').write_text(inventory.stdout+inventory.stderr)
    available=int(subprocess.check_output(ssh+["df -B1 --output=avail / | tail -1"],text=True))
    if available<5*1024**3:raise RuntimeError('At least 5 GiB free root disk required before bootstrap')
    command('test ! -e /opt/torch-venv && sudo mkdir -p '+remote+' && sudo chown ubuntu:ubuntu '+remote,check=True,timeout=15)
    with a.bundle.open('rb') as bundle,(a.output/'bootstrap.log').open('w') as log:
        command('sudo tar -xz -C / opt/torch-venv opt/flyt-cuda-libs',stdin=bundle,stdout=log,stderr=log,check=True,timeout=300)
    transfer=[a.artifacts/'libflyt_guest.so',a.artifacts/'libnuma.so.1',a.artifacts/'libgomp.so.1',a.fixture,
              *(ROOT/'experiments/evidence'/f for f in ['train.py','evidence.py','config.json','guest_training_suite.py'])]
    subprocess.run(scp+[str(f) for f in transfer]+['ubuntu@'+ip+':'+remote+'/'],check=True,timeout=120)
    command('ln -s libflyt_guest.so '+remote+'/libcuda.so.1',check=True,timeout=10)
    discovery="from pathlib import Path; print(chr(10).join(p.name for p in Path('/sys/bus/pci/devices').iterdir() if (p/'vendor').read_text().strip()=='0x1af4' and (p/'device').read_text().strip()=='0x1110'))"
    bdf=subprocess.check_output(ssh+['python3 -c '+shlex.quote(discovery)],text=True,timeout=10).strip()
    if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',bdf):raise ValueError('One ivshmem device required')
    save('channel-bound.json',get('flytsharedmemorychannel',channel))
    subprocess.run([sys.executable,str(ROOT/'scripts/export-shm-guest.py'),'--channel-json',str(a.output/'channel-bound.json'),
                    '--bdf',bdf,'--slot','0','--output',str(a.output/'guest-config')],check=True)
    spec={'backend':'shm-hami','cases':[{'id':f'b{batch}-{mode}','fixture':remote+'/'+a.fixture.name,'batch':batch,'input_mode':mode}
          for batch in [32,1024] for mode in ['resident','transfer']]}
    save('suite-spec.json',spec)
    subprocess.run(scp+[str(a.output/'suite-spec.json'),str(a.output/'guest-config/layout.bin'),'ubuntu@'+ip+':'+remote+'/'],check=True,timeout=30)
    if a.barrier:
        a.barrier.mkdir(parents=True,exist_ok=True);(a.barrier/(a.name+'.ready')).write_text(ip);event('BarrierReady')
        wait(lambda:(a.barrier/'start').exists(),'start barrier',300)
    env=f'FLYT_LAYOUT={remote}/layout.bin FLYT_IVSHMEM_BDF={bdf} FLYT_SLOT=0 FLYT_MIRROR_DEVICE_VA=1 LD_LIBRARY_PATH={remote}:/opt/flyt-cuda-libs LD_PRELOAD={remote}/libflyt_guest.so'
    cmd=f'sudo env {env} /opt/torch-venv/bin/python {remote}/guest_training_suite.py --spec {remote}/suite-spec.json --out {remote}/results --hold-seconds 180'
    with (a.output/'guest-stdout.txt').open('w') as stdout,(a.output/'guest-stderr.txt').open('w') as stderr:
        process=subprocess.Popen(ssh+[cmd],stdout=stdout,stderr=stderr);event('TrainingStarted')
        def channel_ready():
            c=get('flytsharedmemorychannel',channel)
            return c if c and c.get('status',{}).get('phase')=='Ready' else None
        ready=wait(channel_ready,'Channel Ready',60)
        save('channel-ready.json',ready);event('ChannelReady')
        save('worker-running.json',get('pod',channel+'-worker'))
        def complete():
            if command('test -f '+remote+'/results/suite.json',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0:return True
            if process.poll() is not None:raise RuntimeError('Guest ended before results; see stderr')
            return False
        wait(complete,'training results',a.timeout)
        subprocess.run(scp+['-r','ubuntu@'+ip+':'+remote+'/results',str(a.output/'guest-results')],check=True,timeout=120)
        metrics['training']=json.loads((a.output/'guest-results/suite.json').read_text())
        command('sudo touch '+remote+'/results/collected',check=True,timeout=10)
        metrics['guest_exit']=process.wait(timeout=30)
        if metrics['guest_exit'] or metrics['training']['status']!='PASS':raise RuntimeError('Training suite failed')
        metrics['training_pass']=True;event('TrainingPassed')
except Exception as error:
    metrics['error']=str(error);event('Failed',error=str(error))
finally:
    if process and process.poll() is None:process.terminate()
    try:
        save('channel-after.json',get('flytsharedmemorychannel',channel))
        result=subprocess.run(['kubectl','logs',channel+'-worker','-n',ns,'--tail=1000'],capture_output=True,text=True,timeout=20)
        (a.output/'worker.log').write_text(result.stdout+result.stderr)
        k('patch','flytsharedmemorychannel',channel,'-n',ns,'--type=merge','-p','{"spec":{"drain":true}}');event('DrainRequested')
        def released():
            c=get('flytsharedmemorychannel',channel)
            return c if c and c.get('status',{}).get('phase')=='Released' else None
        save('channel-released.json',wait(released,'Released',300));event('Released')
        wait(lambda:get('pod',channel+'-worker') is None,'Worker Pod deletion',60)
        metrics['released']=True
    except Exception as error:metrics['release_error']=str(error)
    metrics['status']='PASS' if metrics.get('training_pass') and metrics.get('released') else 'FAIL'
    save('metrics.json',metrics)
raise SystemExit(0 if metrics['status']=='PASS' else 1)
