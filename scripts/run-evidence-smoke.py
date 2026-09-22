#!/usr/bin/env python3
"""Boot a prepared evidence VM, run its real SHM GPU test, then verify release."""
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
p.add_argument('--name',required=True);p.add_argument('--key',type=Path,required=True)
p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--seed',type=int,default=23);p.add_argument('--timeout',type=int,default=300)
p.add_argument('--probe',choices=['gpu','memory'],default='gpu')
p.add_argument('--scenario',choices=['below','boundary','over','free_reallocate','aggregate_race','suite'])
p.add_argument('--bytes',type=int);p.add_argument('--barrier',type=Path)
a=p.parse_args()
if a.probe=='memory' and (not a.scenario or not a.bytes or a.bytes<=0):p.error('memory requires scenario and positive bytes')
if not re.fullmatch(r'evidence-[a-z0-9-]+',a.name):p.error('evidence VM name required')
a.output.mkdir(parents=True,exist_ok=False)
ns='flyt-evidence';channel=a.name+'-channel';timeline=[];started=time.monotonic();ssh_process=None
def k(args):return subprocess.check_output(['kubectl',*args],text=True,timeout=20)
def get(kind,name):
 text=k(['get',kind,name,'-n',ns,'--ignore-not-found','-o','json'])
 return json.loads(text) if text.strip() else None
def save(name,value):
 (a.output/name).write_text(json.dumps(value,indent=2))
def event(state,**values):
 timeline.append({'elapsed_seconds':time.monotonic()-started,'state':state,**values});save('timeline.json',timeline)
 print(json.dumps(timeline[-1]),flush=True)
def wait_for(function,label,timeout=None):
 deadline=time.monotonic()+(timeout or a.timeout)
 while time.monotonic()<deadline:
  result=function()
  if result:return result
  time.sleep(2)
 raise TimeoutError(label)
initial=get('flytsharedmemorychannel',channel)
if not initial or initial.get('status',{}).get('phase')!='BackingReady':raise ValueError('new BackingReady channel required')
save('channel-before.json',initial)
files=[ROOT/'scripts/run-evidence-smoke.py',ROOT/'experiments/evidence/guest_gpu_smoke.c',ROOT/'experiments/evidence/memory_probe.cu',a.artifacts/'libflyt_guest.so',a.artifacts/('guest-gpu-smoke' if a.probe=='gpu' else 'memory-probe-guest')]
save('manifest.json',{'run_id':a.output.name,'phase':'development','namespace':ns,'channel_uid':initial['metadata']['uid'],
 'channel_spec':initial['spec'],'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
 'file_sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
 'probe':a.probe,'scenario':a.scenario,'bytes':a.bytes,'seed':a.seed,'timeout_seconds':a.timeout})
metrics={'status':'FAIL','formal_training_result':False,'scope':'VM SHM '+a.probe+' smoke and lifecycle',
         'probe':a.probe,'scenario':a.scenario,'bytes':a.bytes}
try:
 k(['patch','vm',a.name,'-n',ns,'--type=merge','-p',json.dumps({'spec':{'runStrategy':'Always'}})])
 event('VMStartRequested')
 def running():
  v=get('vmi',a.name)
  return v if v and v.get('status',{}).get('phase')=='Running' and v['status'].get('interfaces') else None
 vmi=wait_for(running,'VM did not reach Running');save('vmi-running.json',vmi)
 ip=str(ipaddress.ip_address(vmi['status']['interfaces'][0]['ipAddress']))
 ssh=['ssh','-i',str(a.key.resolve()),'-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new',
      '-o','UserKnownHostsFile='+str((a.output/'known-hosts').resolve()),'ubuntu@'+ip]
 def ssh_ready():
  try:return subprocess.run(ssh+['true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8).returncode==0
  except subprocess.TimeoutExpired:return False
 wait_for(ssh_ready,'SSH not ready')
 event('GuestSSHReady',ip=ip)
 discovery="from pathlib import Path; print(chr(10).join(p.name for p in Path('/sys/bus/pci/devices').iterdir() if (p/'vendor').read_text().strip()=='0x1af4' and (p/'device').read_text().strip()=='0x1110'))"
 bdf=subprocess.check_output(ssh+['python3 -c '+shlex.quote(discovery)],text=True,timeout=10).strip()
 if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',bdf):raise ValueError('exactly one ivshmem device required')
 bound=get('flytsharedmemorychannel',channel);save('channel-bound.json',bound)
 subprocess.run([sys.executable,str(ROOT/'scripts/export-shm-guest.py'),'--channel-json',str(a.output/'channel-bound.json'),
                 '--bdf',bdf,'--slot','0','--output',str(a.output/'guest-config')],check=True)
 subprocess.run(ssh+['mkdir -p /tmp/flyt-evidence-run'],check=True,timeout=10)
 scp=['scp','-i',str(a.key.resolve()),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str((a.output/'known-hosts').resolve())]
 binary='guest-gpu-smoke' if a.probe=='gpu' else 'memory-probe-guest'
 subprocess.run(scp+[str(a.artifacts/binary),str(a.artifacts/'libflyt_guest.so'),str(a.output/'guest-config/layout.bin'),'ubuntu@'+ip+':/tmp/flyt-evidence-run/'],check=True,timeout=30)
 if a.barrier:
  a.barrier.mkdir(parents=True,exist_ok=True);(a.barrier/(a.name+'.ready')).write_text(ip)
  event('BarrierReady');wait_for(lambda:(a.barrier/'start').exists(),'start barrier not released')
 arguments=str(a.seed)+' 10' if a.probe=='gpu' else a.scenario+' '+str(a.bytes)
 command='sudo env FLYT_LAYOUT=/tmp/flyt-evidence-run/layout.bin FLYT_IVSHMEM_BDF='+bdf+' FLYT_SLOT=0 FLYT_PROBE_HOLD=10 LD_LIBRARY_PATH=/tmp/flyt-evidence-run /tmp/flyt-evidence-run/'+binary+' '+arguments
 with (a.output/'guest-stdout.txt').open('w') as stdout,(a.output/'guest-stderr.txt').open('w') as stderr:
  ssh_process=subprocess.Popen(ssh+[command],stdout=stdout,stderr=stderr)
  def mapped():
   c=get('flytsharedmemorychannel',channel)
   if c and c.get('status',{}).get('phase')=='Ready':return c
   if ssh_process.poll() is not None:raise RuntimeError('Guest ended before Channel Ready')
   return None
  ready=wait_for(mapped,'Guest/Worker mapping not Ready',60);save('channel-ready.json',ready);event('ChannelReady',bdf=bdf)
  code=ssh_process.wait(timeout=120)
 if code:raise RuntimeError('Guest smoke failed with '+str(code))
 results=[json.loads(line) for line in (a.output/'guest-stdout.txt').read_text().splitlines() if line.startswith('{')]
 if not results or any(x.get('status')!='PASS' for x in results):raise RuntimeError('Guest success evidence missing or failed')
 metrics['probe_results']=results
 event('ProbePassed')
 metrics['probe_pass']=True
except Exception as error:
 metrics['error']=str(error);event('Failed',error=str(error))
finally:
 if ssh_process and ssh_process.poll() is None:
  ssh_process.terminate()
  try:ssh_process.wait(timeout=5)
  except subprocess.TimeoutExpired:ssh_process.kill();ssh_process.wait()
 # Preserve runtime evidence while API objects still exist.
 try:
  save('channel-after-probe.json',get('flytsharedmemorychannel',channel))
  pods=json.loads(k(['get','pods','-n',ns,'-o','json']))['items']
  for pod in pods:
   name=pod['metadata']['name']
   if name==channel+'-worker' or name.startswith('virt-launcher-'+a.name+'-'):
    save(name+'.json',pod)
    for container in pod['spec']['containers']:
     if container['name'] not in ('compute','worker','hook-sidecar-0'):continue
     result=subprocess.run(['kubectl','logs',name,'-n',ns,'-c',container['name'],'--tail=1000'],text=True,capture_output=True,timeout=15)
     (a.output/(name+'-'+container['name']+'.log')).write_text(result.stdout+result.stderr)
 except Exception as error:metrics['evidence_collection_error']=str(error)
 # Drain stops the VM and Worker, including a disconnected SSH workload.
 try:
  k(['patch','flytsharedmemorychannel',channel,'-n',ns,'--type=merge','-p','{"spec":{"drain":true}}'])
  event('DrainRequested')
  def released():
   c=get('flytsharedmemorychannel',channel)
   return c if c and c.get('status',{}).get('phase')=='Released' else None
  final=wait_for(released,'Channel did not release');save('channel-released.json',final);event('Released')
  attachments=json.loads(k(['get','flytchannelattachments','-n',ns,'-o','json']))
  save('attachments-final.json',{'items':[x for x in attachments['items'] if x['spec']['channelRef']['uid']==initial['metadata']['uid']]})
  metrics['released']=True
 except Exception as error:
  metrics['release_error']=str(error);event('ReleaseFailed',error=str(error))
 metrics['status']='PASS' if metrics.get('probe_pass') and metrics.get('released') else 'FAIL'
 metrics['elapsed_seconds']=time.monotonic()-started;save('metrics.json',metrics)
raise SystemExit(0 if metrics['status']=='PASS' else 1)
