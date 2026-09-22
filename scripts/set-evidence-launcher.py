#!/usr/bin/env python3
"""Apply a reversible KubeVirt customization of the launcher used by new VMIs."""
import argparse
import json
from pathlib import Path
import re
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if not re.fullmatch(r'localhost/flyt-virt-launcher@sha256:[0-9a-f]{64}',a.image):p.error('local digest-pinned evidence launcher required')
a.output.mkdir(parents=True,exist_ok=False)
def get(args):return json.loads(subprocess.check_output(['kubectl',*args,'-o','json'],text=True))
kv=get(['get','kubevirt','-n','kubevirt'])['items'][0]
controller=get(['get','deployment','virt-controller','-n','kubevirt'])
argv=controller['spec']['template']['spec']['containers'][0]['args'];index=argv.index('--launcher-image')+1
original=kv['spec'].get('customizeComponents',{})
(a.output/'kubevirt-before.json').write_text(json.dumps(kv,indent=2))
(a.output/'restore-patch.json').write_text(json.dumps({'spec':{'customizeComponents':{**original,'patches':original.get('patches')}}},indent=2))
patch={'resourceType':'Deployment','resourceName':'virt-controller','type':'json',
       'patch':json.dumps([{'op':'replace','path':f'/spec/template/spec/containers/0/args/{index}','value':a.image}])}
custom=dict(original);custom['patches']=list(original.get('patches',[]))+[patch]
payload={'metadata':{'resourceVersion':kv['metadata']['resourceVersion']},'spec':{'customizeComponents':custom}}
(a.output/'apply-patch.json').write_text(json.dumps(payload,indent=2))
subprocess.run(['kubectl','patch','kubevirt',kv['metadata']['name'],'-n','kubevirt','--type=merge','-p',json.dumps(payload)],check=True)
print('Only future VMI launcher selection changes; running VMIs retain their current image.')
