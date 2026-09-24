import json,subprocess,sys,time
from pathlib import Path
root=Path.cwd();base=root/'.local/evidence-20260924';images=json.loads((base/'images.json').read_text());art=base/'artifacts';runs=base/'runs';summary=[]
def call(argv):subprocess.run([str(x) for x in argv],check=True)
def prepare(name,pvc,memory=4096,sessions=1,compute=50):
 dest=runs/name
 call([sys.executable,'scripts/prepare-evidence-vm.py','--name',name,'--reuse-pvc',pvc,'--public-key',art/'guest-key.pub','--control-image',images['control'],'--worker-image',images['worker'],'--hook-image',images['hook'],'--memory-mib',memory,'--compute',compute,'--sessions',sessions,'--output',dest/'prepare'])
 call(['kubectl','wait','-n','flyt-evidence','flytsharedmemorychannel/'+name+'-channel','--for=jsonpath={.status.phase}=BackingReady','--timeout=180s'])
 return dest
def launch(name,dest,extra):
 log=(dest/'runner.log').open('w')
 proc=subprocess.Popen([sys.executable,'scripts/run-evidence-smoke.py','--name',name,'--key',str(art/'guest-key'),'--artifacts',str(art),'--output',str(dest/'run'),*map(str,extra)],stdout=log,stderr=subprocess.STDOUT)
 return name,dest,proc,log
def finish(job):
 name,dest,proc,log=job;code=proc.wait();log.close();m=json.loads((dest/'run/metrics.json').read_text())
 pods=json.loads(subprocess.check_output(['kubectl','get','pods','-n','flyt-evidence','-o','json'],text=True))['items']
 residual=[p['metadata']['name'] for p in pods if p['metadata']['name']==name+'-channel-worker' or p['metadata']['name'].startswith('virt-launcher-'+name+'-')]
 row={'name':name,'exit_code':code,'metrics':m,'residual_execution_pods':residual};summary.append(row);(base/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(row),flush=True)
 if code or residual:raise RuntimeError('Failed run; do not reuse PVC: '+name)
 # Keep released Channel/VM records available to the live dashboard.
 return row
pvcs=['evidence-vm-a-backing','evidence-pair-b-backing']
barrier=base/'pair-barrier'
jobs=[]
for i in range(2):
 name='evidence-show-pair-'+('a' if i==0 else 'b');dest=prepare(name,pvcs[i]);jobs.append(launch(name,dest,['--seed',2026+i,'--gpu-seconds',45,'--barrier',barrier]))
deadline=time.monotonic()+300
while len(list(barrier.glob('*.ready')))!=2:
 if time.monotonic()>deadline:raise TimeoutError('pair barrier')
 if any(j[2].poll() is not None for j in jobs):raise RuntimeError('pair exited before barrier')
 time.sleep(1)
(barrier/'start').touch()
for job in jobs:finish(job)
for quota in [1024,4096]:
 for scenario in ['observe','suite']:
  name=f'evidence-show-mem-{quota}-{scenario}';dest=prepare(name,pvcs[0],quota,compute=100);finish(launch(name,dest,['--probe','memory','--scenario',scenario,'--bytes',quota*1024*1024]))
name='evidence-show-aggregate';dest=prepare(name,pvcs[0],4096,sessions=2,compute=100);finish(launch(name,dest,['--probe','memory','--scenario','aggregate_race','--bytes',4294967296]))
for offset in range(0,20,2):
 jobs=[]
 for i in range(offset,offset+2):
  name=f'evidence-show-life-{i+1:02}';dest=prepare(name,pvcs[i%2]);jobs.append(launch(name,dest,['--seed',3000+i]))
 for job in jobs:finish(job)
(base/'RUNS_DONE').touch()
