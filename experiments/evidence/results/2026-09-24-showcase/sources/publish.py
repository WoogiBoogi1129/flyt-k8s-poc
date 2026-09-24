"""Task-specific explicit allowlist export; no .local directory-wide copying."""
import gzip,hashlib,json,shutil,subprocess
from pathlib import Path
b=Path('.local/evidence-20260924');dest=Path('experiments/evidence/results/2026-09-24-showcase');dest.mkdir(exist_ok=False)
subprocess.run(['python3','experiments/evidence/export_development.py','--source',str(b/'runs'),'--output',str(dest/'runs')],check=True)
for directory in ['screenshots','plots']:
 (dest/directory).mkdir()
 for f in (b/directory).iterdir():
  if f.suffix in ['.json','.png','.csv']:shutil.copyfile(f,dest/directory/f.name)
for f in (b/'additional-screenshots').iterdir():
 if f.suffix in ['.png','.json']:
  target=dest/'screenshots'/f.name
  if target.exists():target=dest/'screenshots'/('additional-'+f.name)
  shutil.copyfile(f,target)
shutil.copytree(b/'compute-settings',dest/'compute-settings')
shutil.copytree(b/'compute-libraries',dest/'compute-libraries')
shutil.copytree(b/'video',dest/'video')
for name in ['summary.json','images.json','source-artifact-hashes.json','compute-protocol.json','image-retention-pod.json','performance-readiness.json','environment.json']:
 shutil.copyfile(b/name,dest/name)
shutil.copyfile(b/'audit/final-audit.json',dest/'final-audit.json')
(dest/'checks').mkdir()
for name in ['control-tests.log','evidence-tests.log','export-tests.log']:
 shutil.copyfile(b/name,dest/'checks'/name)
(dest/'sources').mkdir()
shutil.copyfile(b/'run-evidence-smoke-observe.py',dest/'sources/run-evidence-smoke-observe.py')
# These contain no credentials; they show exact coordination used before the
# reusable checked-in coordinator was finalized.
for name in ['run.py','resume.py','compute-runs.py','process-observer.py','audit.py','publish.py','compute-settings-observer.py','capture-evidence-dashboard-v1.cjs']:
 shutil.copyfile(b/name,dest/'sources'/name)
for directory in ['snapshots','snapshots-host']:
 with gzip.open(dest/(directory+'.jsonl.gz'),'wt') as out:
  for f in sorted((b/directory).glob('*.json')):
   d=json.loads(f.read_text());out.write(json.dumps(d)+'\n')
with gzip.open(dest/'process-cgroups.jsonl.gz','wb') as out:out.write((b/'process-cgroups.jsonl').read_bytes())
events=json.loads((b/'image-gc-events.json').read_text())['items']
selected=[{k:x.get(k) for k in ['reason','message','type','firstTimestamp','lastTimestamp','count']}|{'object':x.get('involvedObject',{}).get('name')} for x in events if x.get('involvedObject',{}).get('name','').startswith(('evidence-show-','virt-launcher-evidence-show-')) and x.get('type')=='Warning']
(dest/'image-preparation-warnings.json').write_text(json.dumps(selected,indent=2)+'\n')
# Resource and image fields only. Exclude env, volumes, cloud-init and tokens.
devices=[]
for f in sorted((b/'runs').glob('*/run/vmi-running.json')):
 x=json.loads(f.read_text());device=x['spec']['domain'].get('devices',{})
 devices.append({'run':f.parent.parent.name,'vmi_uid':x['metadata']['uid'],'phase':x['status']['phase'],'gpus':device.get('gpus',[]),'host_devices':device.get('hostDevices',[]),'cpu':x['spec']['domain'].get('cpu',{}),'memory':x['spec']['domain'].get('resources',{})})
(dest/'vmi-device-inventory.json').write_text(json.dumps(devices,indent=2)+'\n')
resources=[]
for f in sorted((b/'runs').glob('*/run/*-worker.json')):
 x=json.loads(f.read_text())
 resources.append({'run':f.parent.parent.name,'pod':x['metadata']['name'],'uid':x['metadata']['uid'],
 'containers':[{'name':c['name'],'resources':c.get('resources',{}),'image':c['image']} for c in x['spec']['containers']],
 'container_ids':[c.get('containerID') for c in x.get('status',{}).get('containerStatuses',[])]})
(dest/'worker-resources.json').write_text(json.dumps(resources,indent=2)+'\n')
# Connect host GPU PIDs to recorded Worker Pod UIDs, never process-name guesses.
import re
workers={r['uid']:r for r in resources};mapping={};unknown=set()
for line in (b/'process-cgroups.jsonl').read_text().splitlines():
 row=json.loads(line)
 for process in row['processes']:
  match=re.search(r'pod([a-f0-9_]+)\.slice',process.get('cgroup',''))
  uid=match.group(1).replace('_','-') if match else None
  if uid not in workers:
   unknown.add(process['pid']);continue
  key=(process['pid'],uid)
  if key not in mapping:mapping[key]={'pid':process['pid'],'worker_uid':uid,'worker':workers[uid]['pod'],'run':workers[uid]['run'],'first_seen':row['timestamp'],'cgroup':process['cgroup']}
  mapping[key]['last_seen']=row['timestamp']
(dest/'gpu-process-worker-map.json').write_text(json.dumps({'method':'host /proc/PID/cgroup matched to saved Worker Pod UID','mapped':list(mapping.values()),'unmatched_pids':sorted(unknown)},indent=2)+'\n')
print(dest)
