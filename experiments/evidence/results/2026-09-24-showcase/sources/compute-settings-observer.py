import json,subprocess,time,urllib.request
from pathlib import Path
b=Path('.local/evidence-20260924');out=b/'compute-settings';out.mkdir(exist_ok=True);seen=set()
code="import os,json; print(json.dumps({k:os.environ.get(k) for k in ['CUDA_DEVICE_SM_LIMIT','CUDA_DEVICE_SM_LIMIT_0','CUDA_DEVICE_MEMORY_LIMIT','CUDA_DEVICE_MEMORY_LIMIT_0','GPU_CORE_UTILIZATION_POLICY','LD_PRELOAD']}))"
while not (b/'STOP_OBSERVERS').exists():
 try:
  d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/snapshot',timeout=5))
  for r in d['routes']:
   if '-compute-' not in r['vm'] or r['phase']!='Ready' or r['vm'] in seen:continue
   p=subprocess.run(['kubectl','exec','-n','flyt-evidence',r['worker'],'--','python3','-c',code],text=True,capture_output=True,timeout=10)
   if not p.returncode:
    (out/(r['vm']+'.json')).write_text(json.dumps({'snapshot_timestamp':d['timestamp'],'route':r,'selected_runtime_environment':json.loads(p.stdout)},indent=2));seen.add(r['vm'])
 except Exception as e:print(str(e),flush=True)
 time.sleep(2)
