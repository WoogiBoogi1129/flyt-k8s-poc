import datetime,json,subprocess,time
from pathlib import Path
b=Path('.local/evidence-20260924');out=b/'process-cgroups.jsonl'
with out.open('a') as f:
 while not (b/'STOP_OBSERVERS').exists():
  p=subprocess.run(['nvidia-smi','-i','GPU-7d708c42-8d4a-16d5-0746-474567157aa3','--query-compute-apps=pid','--format=csv,noheader,nounits'],text=True,capture_output=True)
  rows=[]
  for x in p.stdout.splitlines():
   if x.strip().isdigit():
    pid=int(x)
    try:rows.append({'pid':pid,'cgroup':Path(f'/proc/{pid}/cgroup').read_text()})
    except OSError as e:rows.append({'pid':pid,'error':str(e)})
  f.write(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':p.returncode,'processes':rows})+'\n');f.flush();time.sleep(2)
