#!/usr/bin/env python3
"""Repeat real VM GPU smoke/release with fresh allocations on two released PVCs.

Development evidence only: this does not run or claim PyTorch training.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--images',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--count',type=int,default=20)
p.add_argument('--pvc-a',default='evidence-vm-a-backing')
p.add_argument('--pvc-b',default='evidence-pair-b-backing')
a=p.parse_args()
if not 1<=a.count<=20:p.error('count must be 1..20')
if a.pvc_a==a.pvc_b:p.error('distinct PVCs required')
a.output.mkdir(parents=True,exist_ok=False)
images=json.loads(a.images.read_text());results=[]
for offset in range(0,a.count,2):
    jobs=[]
    for i in range(offset,min(offset+2,a.count)):
        name='evidence-life-'+str(i+1).zfill(2)
        dest=a.output/name
        subprocess.run([sys.executable,str(ROOT/'scripts/prepare-evidence-vm.py'),
            '--name',name,'--reuse-pvc',a.pvc_a if i%2==0 else a.pvc_b,
            '--public-key',str(a.artifacts/'guest-key.pub'),
            '--control-image',images['control'],'--worker-image',images['worker'],
            '--hook-image',images['hook'],'--compute','50','--output',str(dest/'prepare')],check=True)
        subprocess.run(['kubectl','wait','-n','flyt-evidence','flytsharedmemorychannel/'+name+'-channel',
            '--for=jsonpath={.status.phase}=BackingReady','--timeout=60s'],check=True)
        log=(dest/'runner.log').open('w')
        proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/run-evidence-smoke.py'),
            '--name',name,'--key',str(a.artifacts/'guest-key'),'--artifacts',str(a.artifacts),
            '--seed',str(2026+i),'--output',str(dest/'run')],stdout=log,stderr=subprocess.STDOUT)
        jobs.append((name,dest,proc,log))
    failed=False
    for name,dest,proc,log in jobs:
        code=proc.wait();log.close()
        metrics=dest/'run/metrics.json'
        row={'name':name,'exit_code':code,'metrics':json.loads(metrics.read_text()) if metrics.exists() else None}
        results.append(row);print(json.dumps(row),flush=True)
        (a.output/'summary.json').write_text(json.dumps(results,indent=2))
        failed|=code!=0
    if failed:raise SystemExit('Stopped after failed round; inspect evidence before reusing PVCs')
