import json,subprocess,sys,time
from pathlib import Path
base=Path('.local/evidence-20260924').resolve()
# Sequential GPU use: wait until all sharing/memory/lifecycle work has finished.
while not (base/'RUNS_DONE').exists():time.sleep(3)
src=(base/'run.py').read_text();prefix=src[:src.index("pvcs=['evidence-vm-a-backing'")].replace('summary=[]','summary=json.loads((base/"summary.json").read_text())')
exec(prefix)
# Repeat simultaneous execution after switching observation to the GPU host.
barrier=base/'capture-pair-barrier';pair=[]
for i in range(2):
 name='evidence-show-capture-'+('a' if i==0 else 'b')
 dest=prepare(name,['evidence-vm-a-backing','evidence-pair-b-backing'][i])
 pair.append(launch(name,dest,['--seed',4026+i,'--gpu-seconds',45,'--barrier',barrier]))
deadline=time.monotonic()+300
while len(list(barrier.glob('*.ready')))!=2:
 if time.monotonic()>deadline or any(j[2].poll() is not None for j in pair):raise RuntimeError('capture pair barrier failed')
 time.sleep(1)
(barrier/'start').touch()
for job in pair:finish(job)
for repetition in range(1,4):
 for compute in ([100,50,25] if repetition%2 else [25,50,100]):
  name=f'evidence-show-compute-{compute}-{repetition}'
  dest=prepare(name,'evidence-vm-a-backing',4096,compute=compute)
  finish(launch(name,dest,['--gpu-program','compute','--gpu-seconds',71]))
(base/'COMPUTE_DONE').touch()
