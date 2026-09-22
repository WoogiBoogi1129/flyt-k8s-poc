#!/usr/bin/env python3
"""Inject a scoped fault into a fresh two-VM SHM GPU development test.

VM B validates PTX results during the fault. This is not E5 PyTorch training.
The ordinary smoke runner is expected to report FAIL for a terminated victim;
this orchestrator records the fault experiment's separate verdict.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];NS='flyt-evidence'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--images',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--mode',choices=['guest-process','worker-process','worker-pod','vmi-force','controller'],required=True)
p.add_argument('--repeat',type=int,default=5)
a=p.parse_args()
if not 1<=a.repeat<=5:p.error('repeat must be 1..5')
a.output.mkdir(parents=True,exist_ok=False);images=json.loads(a.images.read_text());summary=[]
def k(args):return subprocess.check_output(['kubectl',*args],text=True,timeout=70)
def obj(kind,name):return json.loads(k(['get',kind,name,'-n',NS,'-o','json']))
def save(path,value):path.write_text(json.dumps(value,indent=2))
for rep in range(1,a.repeat+1):
    run=a.output/str(rep);run.mkdir();barrier=run/'barrier';barrier.mkdir();jobs=[];record={'mode':a.mode,'repeat':rep,'status':'FAIL','phase':'development'}
    try:
        for role,pvc,seed in [('a','evidence-vm-a-backing',2026),('b','evidence-pair-b-backing',2027)]:
            name=f'evidence-fault-{a.mode}-{rep}-{role}';dest=run/role
            subprocess.run([sys.executable,str(ROOT/'scripts/prepare-evidence-vm.py'),'--name',name,'--reuse-pvc',pvc,
                '--public-key',str(a.artifacts/'guest-key.pub'),'--control-image',images['control'],
                '--worker-image',images['worker'],'--hook-image',images['hook'],'--compute','50','--output',str(dest/'prepare')],check=True)
            k(['wait','-n',NS,'flytsharedmemorychannel/'+name+'-channel','--for=jsonpath={.status.phase}=BackingReady','--timeout=60s'])
            log=(dest/'runner.log').open('w')
            proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/run-evidence-smoke.py'),'--name',name,
                '--key',str(a.artifacts/'guest-key'),'--artifacts',str(a.artifacts),'--seed',str(seed),
                '--barrier',str(barrier),'--output',str(dest/'run')],stdout=log,stderr=subprocess.STDOUT)
            jobs.append((name,dest,proc,log))
        deadline=time.monotonic()+300
        while len(list(barrier.glob('*.ready')))!=2:
            if time.monotonic()>deadline or any(proc.poll() is not None for _,_,proc,_ in jobs):raise RuntimeError('guest start barrier failed')
            time.sleep(1)
        (barrier/'start').write_text('start')
        deadline=time.monotonic()+30
        while True:
            channels=[obj('flytsharedmemorychannel',name+'-channel') for name,_,_,_ in jobs]
            if all(c.get('status',{}).get('phase')=='Ready' for c in channels):break
            if time.monotonic()>deadline or any(proc.poll() is not None for _,_,proc,_ in jobs):raise RuntimeError('both channels did not become Ready')
            time.sleep(.2)
        save(run/'channels-before-fault.json',channels)
        victim=jobs[0][0];injected=time.monotonic()
        if a.mode=='guest-process':
            ip=str(ipaddress.ip_address((barrier/(victim+'.ready')).read_text().strip()))
            cmd=['ssh','-i',str(a.artifacts/'guest-key'),'-o','BatchMode=yes','-o','ConnectTimeout=5',
                 '-o','UserKnownHostsFile='+str(jobs[0][1]/'run/known-hosts'),'ubuntu@'+ip,'sudo pkill -KILL -x guest-gpu-smoke']
        elif a.mode in ('worker-process','worker-pod'):
            pod=obj('pod',victim+'-channel-worker')
            if pod['metadata']['uid']!=channels[0]['status']['workerPodUID']:raise RuntimeError('worker UID changed')
            if a.mode=='worker-process':
                code="import pathlib,os,signal\nmatches=[]\nfor p in pathlib.Path('/proc').iterdir():\n if not p.name.isdigit():continue\n try: command=(p/'cmdline').read_bytes().split(bytes([0]))[0]\n except (FileNotFoundError,ProcessLookupError):continue\n if command==b'/opt/flyt/bin/flyt-shm-worker':matches.append(int(p.name))\nassert len(matches)==1,matches\nos.kill(matches[0],signal.SIGKILL)\nprint(matches)"
                cmd=['kubectl','exec','-n',NS,pod['metadata']['name'],'--','python3','-c',code]
            else:cmd=['kubectl','delete','pod',pod['metadata']['name'],'-n',NS,'--force','--grace-period=0','--wait=false']
        elif a.mode=='vmi-force':
            vmi=obj('vmi',victim)
            if vmi['metadata']['uid']!=channels[0]['status']['vmiUID']:raise RuntimeError('VMI UID changed')
            cmd=['kubectl','delete','vmi',victim,'-n',NS,'--force','--grace-period=0','--wait=false']
        else:
            pods=json.loads(k(['get','pods','-n',NS,'-l','app=evidence-flyt-controller','-o','json']))['items']
            if len(pods)!=1:raise RuntimeError('one experiment controller required')
            record['old_controller_uid']=pods[0]['metadata']['uid']
            cmd=['kubectl','delete','pod',pods[0]['metadata']['name'],'-n',NS,'--wait=false']
        result=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
        save(run/'injection.json',{'command':cmd,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise RuntimeError('fault injection failed')
        record['injected']=True
        codes=[]
        for name,dest,proc,log in jobs:
            codes.append(proc.wait(timeout=660));log.close()
        record['seconds_after_injection']=time.monotonic()-injected
        metrics=[json.loads((dest/'run/metrics.json').read_text()) for _,dest,_,_ in jobs]
        record['victim']=metrics[0];record['observer']=metrics[1]
        expected=metrics[0]['status']=='PASS' if a.mode=='controller' else metrics[0]['status']=='FAIL' and not metrics[0].get('probe_pass')
        passed=expected and metrics[1]['status']=='PASS' and all(m.get('released') for m in metrics)
        if a.mode=='controller':
            pods=json.loads(k(['get','pods','-n',NS,'-l','app=evidence-flyt-controller','-o','json']))['items']
            record['new_controller_uids']=[x['metadata']['uid'] for x in pods]
            passed &= len(pods)==1 and pods[0]['metadata']['uid']!=record['old_controller_uid'] and any(c['type']=='Ready' and c['status']=='True' for c in pods[0].get('status',{}).get('conditions',[]))
        record['status']='PASS' if passed else 'FAIL'
        if passed:
            # Runtime snapshots are already durable in each run directory.
            # Remove only released fixtures so historical CRs do not dominate
            # the polling controller while subsequent trials run.
            for name,_,_,_ in jobs:
                c=obj('flytsharedmemorychannel',name+'-channel')
                if c.get('status',{}).get('phase')!='Released':raise RuntimeError('fixture is not released')
                k(['delete','flytsharedmemorychannel',name+'-channel','-n',NS,'--wait=true','--timeout=60s'])
                for kind,suffix in [('vm',''),('flytgpurequest','-request'),('flytgpuprofile','-profile')]:
                    k(['delete',kind,name+suffix,'-n',NS,'--wait=true','--timeout=60s'])
            record['fixture_cleanup']=True
    except Exception as error:
        record['error']=str(error)
    finally:
        # Runners own normal cleanup. On orchestration errors request drain of
        # only these fresh channels, then allow their finally blocks to finish.
        for name,dest,proc,log in jobs:
            if proc.poll() is None:
                try:k(['patch','flytsharedmemorychannel',name+'-channel','-n',NS,'--type=merge','-p','{"spec":{"drain":true}}'])
                except Exception:pass
                try:proc.wait(timeout=660)
                except subprocess.TimeoutExpired:record['cleanup_timeout']=True
            log.close()
        save(run/'metrics.json',record);summary.append(record);save(a.output/'summary.json',summary);print(json.dumps(record),flush=True)
    if record['status']!='PASS':raise SystemExit('Stopped after failed fault case; inspect state before reuse')
