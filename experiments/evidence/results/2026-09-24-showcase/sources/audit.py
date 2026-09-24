import datetime,json,subprocess,time
from pathlib import Path
b=Path('.local/evidence-20260924');out=b/'audit';out.mkdir(exist_ok=False)
def k(*args):return subprocess.check_output(['kubectl',*args],text=True,timeout=120)
def get(kind,namespace):return json.loads(k('get',kind,'-n',namespace,'-o','json'))
channels=get('flytsharedmemorychannels','flyt-evidence')['items'];own=[c for c in channels if c['metadata']['name'].startswith('evidence-show-')]
assert own and all(c['status']['phase']=='Released' for c in own)
pods=get('pods','flyt-evidence')['items'];vmis=get('vmi','flyt-evidence')['items']
residual=[p['metadata']['name'] for p in pods if p['metadata']['name'].startswith('evidence-show-') and (p['metadata']['name'].endswith('-worker') or p['status']['phase'] not in ['Succeeded','Failed'])]
assert not residual and not any(v['metadata']['name'].startswith('evidence-show-') for v in vmis)
old=json.loads((b/'channels-before.json').read_text())['items'];after=get('flytsharedmemorychannels','flyt-gpu-validation')['items']
preserved=[]
for c in old:
 if c['metadata']['namespace']!='flyt-gpu-validation':continue
 cur=next(x for x in after if x['metadata']['uid']==c['metadata']['uid']);preserved.append({'name':cur['metadata']['name'],'uid':cur['metadata']['uid'],'before':c['status']['phase'],'after':cur['status']['phase']});assert cur['status']['phase']==c['status']['phase']
oldpods=json.loads((b/'pods-before.json').read_text())['items'];oldbasic=next(x for x in oldpods if x['metadata']['name'].startswith('virt-launcher-basic-vm-'))
currentbasic=next(x for x in get('pods','flyt-infra-validation')['items'] if x['metadata']['uid']==oldbasic['metadata']['uid']);assert currentbasic['status']['phase']=='Running'
image=json.loads((b/'images.json').read_text())['control'];name='evidence-show-backing-audit'
code="import json,pathlib; print(json.dumps({x:sorted(str(p.relative_to(pathlib.Path('/'+x))) for p in pathlib.Path('/'+x).rglob('*')) for x in ['a','b']}))"
pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'flyt-evidence'},'spec':{'nodeName':'gpu-4','restartPolicy':'Never','automountServiceAccountToken':False,'securityContext':{'runAsUser':107,'runAsGroup':107},'containers':[{'name':'audit','image':image,'command':['python3','-c',code],'volumeMounts':[{'name':x,'mountPath':'/'+x,'readOnly':True} for x in ['a','b']]}],'volumes':[{'name':x,'persistentVolumeClaim':{'claimName':n,'readOnly':True}} for x,n in [('a','evidence-vm-a-backing'),('b','evidence-pair-b-backing')]]}}
created=json.loads(subprocess.check_output(['kubectl','create','-f','-','-o','json'],input=json.dumps(pod),text=True))
try:
 k('wait','pod/'+name,'-n','flyt-evidence','--for=jsonpath={.status.phase}=Succeeded','--timeout=90s')
 listing=json.loads(k('logs',name,'-n','flyt-evidence'));assert listing=={'a':[],'b':[]}
finally:
 cur=json.loads(k('get','pod',name,'-n','flyt-evidence','-o','json'));assert cur['metadata']['uid']==created['metadata']['uid']
 k('delete','pod',name,'-n','flyt-evidence','--wait=true','--timeout=60s')
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_gpu_memory','--format=csv'],text=True)
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,memory.used','--format=csv'],text=True)
assert 'GPU-7d708c42-8d4a-16d5-0746-474567157aa3' not in apps, apps
alloc=[c['status']['allocation'] for c in own];assert len(alloc)==len(set(alloc))
life=[c for c in own if '-life-' in c['metadata']['name']];assert len(life)==20
result={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS','channels_released':len(own),'unique_allocations':len(set(alloc)),'lifecycle_count':len(life),'residual_execution_pods':residual,'backing_listing':listing,'preserved_channels':preserved,'basic_vm_launcher_uid':currentbasic['metadata']['uid'],'basic_vm_phase':currentbasic['status']['phase'],'gpu_processes':apps,'gpu_memory':gpu}
(out/'final-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
# Delete only the temporary image-retention Pod created for this invocation.
ret=json.loads(k('get','pod','evidence-image-retention-20260924','-n','flyt-evidence','-o','json'))
assert ret['metadata']['uid']==json.loads((b/'image-retention-created.json').read_text())['uid']
(out/'image-retention-identity.json').write_text(json.dumps({'name':ret['metadata']['name'],'uid':ret['metadata']['uid']}))
k('delete','pod',ret['metadata']['name'],'-n','flyt-evidence','--wait=true','--timeout=90s')
