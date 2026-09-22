#!/usr/bin/env python3
"""Create one new, halted evidence VM and its UID-bound SHM allocation request."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--name',required=True);p.add_argument('--storage-slot',choices=['a','b'])
p.add_argument('--reuse-pvc',help='Reuse an existing PVC only after every previous channel using it is Released')
p.add_argument('--public-key',type=Path,required=True);p.add_argument('--control-image',required=True)
p.add_argument('--worker-image',required=True)
p.add_argument('--hook-image',required=True)
p.add_argument('--gpu-uuid',default='GPU-7d708c42-8d4a-16d5-0746-474567157aa3')
p.add_argument('--memory-mib',type=int,default=4096);p.add_argument('--compute',type=int,default=100)
p.add_argument('--sessions',type=int,default=1);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();name=a.name
if bool(a.storage_slot)==bool(a.reuse_pvc):p.error('choose storage-slot or reuse-pvc')
if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}',name):p.error('invalid name')
if not 1<=a.sessions<=32 or not 1<=a.compute<=100 or a.memory_mib<=0:p.error('invalid quota')
for image in [a.control_image,a.worker_image,a.hook_image]:
 if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}',image):p.error('digest-pinned images required')
key=a.public_key.read_text().strip()
if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',key):p.error('expected public Ed25519 key')
a.output.mkdir(parents=True,exist_ok=False)
namespace='flyt-evidence'
def meta(n):return {'name':n,'namespace':namespace,'labels':{'app.kubernetes.io/part-of':'flyt-evidence'}}
def create(obj):
 (a.output/(obj['kind']+'-'+obj['metadata']['name']+'.json')).write_text(json.dumps(obj,indent=2))
 output=subprocess.check_output(['kubectl','create','-f','-','-o','json'],input=json.dumps(obj),text=True)
 actual=json.loads(output);print(obj['kind'],actual['metadata']['name'],actual['metadata']['uid'])
 return {'name':actual['metadata']['name'],'uid':actual['metadata']['uid']}
pvname=name+'-backing'
if a.reuse_pvc:
 claim=json.loads(subprocess.check_output(['kubectl','get','pvc',a.reuse_pvc,'-n',namespace,'-o','json'],text=True))
 if claim['status']['phase']!='Bound':raise ValueError('PVC is not Bound')
 channels=json.loads(subprocess.check_output(['kubectl','get','flytsharedmemorychannels','-n',namespace,'-o','json'],text=True))['items']
 if any(c['spec']['pvcRef']['uid']==claim['metadata']['uid'] and c.get('status',{}).get('phase')!='Released' for c in channels):
  raise ValueError('Previous allocation not Released')
 pvc={'name':claim['metadata']['name'],'uid':claim['metadata']['uid']}
else:
 path='/var/lib/flyt-evidence-20260922/'+a.storage_slot
 volumes=json.loads(subprocess.check_output(['kubectl','get','pv','-o','json'],text=True))['items']
 if any(v['spec'].get('local',{}).get('path')==path for v in volumes):raise ValueError('Path already belongs to a PV; use reuse-pvc after release')
 create({'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':pvname},'spec':{'capacity':{'storage':'2Gi'},'accessModes':['ReadWriteOnce'],
 'persistentVolumeReclaimPolicy':'Retain','storageClassName':'','local':{'path':'/var/lib/flyt-evidence-20260922/'+a.storage_slot},
 'claimRef':{'namespace':namespace,'name':pvname},
 'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':['gpu-4']}]}]}}}})
 pvc=create({'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':meta(pvname),'spec':{'storageClassName':'','volumeName':pvname,'accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':'2Gi'}}}})
vm=create({'apiVersion':'kubevirt.io/v1','kind':'VirtualMachine','metadata':meta(name),'spec':{'runStrategy':'Halted','template':{'metadata':{'labels':{'app.kubernetes.io/part-of':'flyt-evidence'}},'spec':{
 'nodeSelector':{'kubernetes.io/hostname':'gpu-4'},'domain':{'cpu':{'cores':8},'resources':{'requests':{'memory':'16Gi'}},
 'devices':{'disks':[{'name':'root','disk':{'bus':'virtio'}},{'name':'cloudinit','disk':{'bus':'virtio'}}],'interfaces':[{'name':'default','masquerade':{}}]}},
 'networks':[{'name':'default','pod':{}}],'volumes':[{'name':'root','containerDisk':{'image':'quay.io/containerdisks/ubuntu@sha256:27d3bbe1374521aa43fc50b647d712c9c90693f1e8ce9516aa25aeb73f17681d'}},
 {'name':'cloudinit','cloudInitNoCloud':{'userData':'#cloud-config\nusers:\n  - name: ubuntu\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    shell: /bin/bash\n    ssh_authorized_keys:\n      - '+key+'\nssh_pwauth: false\n'}}]}}}})
profile=create({'apiVersion':'flyt.dev/v1alpha1','kind':'FlytGPUProfile','metadata':meta(name+'-profile'),'spec':{'approved':True,'nodeName':'gpu-4','gpuUUID':a.gpu_uuid,
 'cores':a.compute,'memoryMiB':a.memory_mib,'maxClients':a.sessions,'workerImage':a.worker_image,'hamiNamespace':'kube-system','schedulerName':'hami-scheduler','runtimeClass':'nvidia'}})
request=create({'apiVersion':'flyt.dev/v1alpha1','kind':'FlytGPURequest','metadata':meta(name+'-request'),'spec':{'vmRef':vm,'profileRef':profile,'count':1,'compute':a.compute,'memory':str(a.memory_mib)+'Mi'}})
create({'apiVersion':'flyt.dev/v1alpha1','kind':'FlytSharedMemoryChannel','metadata':meta(name+'-channel'),'spec':{'vmRef':vm,'requestRef':request,'pvcRef':pvc,'sessions':a.sessions,
 'uid':107,'gid':107,'image':a.control_image,'workerImage':a.worker_image,'hookImage':a.hook_image}})
