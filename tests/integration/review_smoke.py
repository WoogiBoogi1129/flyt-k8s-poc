#!/usr/bin/env python3
"""Real-cluster stage-one test. Only use a dedicated review release/namespace.

Creates and removes its own CRs, a Halted VM, and an unmounted local PV/PVC.
No worker, VMI, backing directory, or CUDA process is created by this test.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

p=argparse.ArgumentParser()
p.add_argument('--namespace',required=True)
p.add_argument('--release',required=True)
p.add_argument('--node',required=True)
p.add_argument('--report',type=Path,required=True)
a=p.parse_args()
name='review-check-'+uuid.uuid4().hex[:8]
results=[];created=[]

def kubectl(*args,input=None,check=True):
    r=subprocess.run(['kubectl',*args],input=None if input is None else json.dumps(input),text=True,capture_output=True)
    if check and r.returncode: raise RuntimeError(r.stderr)
    return r

def get(kind,n):
    return json.loads(kubectl('get',kind,n,'-n',a.namespace,'-o','json').stdout)

def create(obj,cluster=False):
    args=['create','-f','-','-o','json']
    if not cluster:args+=['-n',a.namespace]
    out=json.loads(kubectl(*args,input=obj).stdout)
    created.append((obj['kind'],obj['metadata']['name'],cluster))
    return out

def ref(o):return {'name':o['metadata']['name'],'uid':o['metadata']['uid']}
def resource(kind,spec,api='flyt.dev/v1alpha1',suffix=''):
    return {'apiVersion':api,'kind':kind,'metadata':{'name':name+suffix},'spec':spec}
def wait(fn,timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(1)
    raise TimeoutError('condition did not become true')
def passed(message):results.append(message);print('PASS',message,flush=True)

try:
    deployment=get('deployment',a.release+'-flyt-controller')
    env={x['name']:x.get('value') for x in deployment['spec']['template']['spec']['containers'][0]['env']}
    assert env['FLYT_MODE']=='review','test only runs in review mode'
    sa='system:serviceaccount:'+a.namespace+':'+deployment['spec']['template']['spec']['serviceAccountName']
    for verb,resource_name in [('create','pods'),('update','virtualmachines.kubevirt.io'),('create','flytchannelattachments.flyt.dev')]:
        r=kubectl('auth','can-i',verb,resource_name,'-n',a.namespace,'--as='+sa,check=False)
        assert r.stdout.strip()=='no',(verb,resource_name,r.stdout)
    passed('review RBAC denies workload and attachment mutations')
    image='example.invalid/flyt@sha256:'+'a'*64
    missing={'name':name+'-missing','uid':str(uuid.uuid4())}
    bad=resource('FlytSharedMemoryChannel',{'vmRef':missing,'requestRef':missing,'pvcRef':missing,
        'sessions':0,'uid':65532,'gid':65532,'image':image,'workerImage':image,'hookImage':image},suffix='-invalid')
    assert kubectl('create','-f','-','-n',a.namespace,input=bad,check=False).returncode
    passed('CRD rejects invalid session count')
    vm=create(resource('VirtualMachine',{'runStrategy':'Halted','template':{'metadata':{},'spec':{
        'domain':{'resources':{'requests':{'memory':'128Mi'}},'devices':{}},'volumes':[]}}},api='kubevirt.io/v1'))
    pv=create(resource('PersistentVolume',{'capacity':{'storage':'64Mi'},'accessModes':['ReadWriteOnce'],
        'persistentVolumeReclaimPolicy':'Retain','storageClassName':'',
        'local':{'path':'/srv/'+name+'-never-mounted'},'nodeAffinity':{'required':{'nodeSelectorTerms':[
            {'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[a.node]}]}]}}},api='v1'),cluster=True)
    pvc=create(resource('PersistentVolumeClaim',{'storageClassName':'','volumeName':name,'accessModes':['ReadWriteOnce'],
        'resources':{'requests':{'storage':'64Mi'}}},api='v1'))
    wait(lambda:get('pvc',name).get('status',{}).get('phase')=='Bound')
    profile=create(resource('FlytGPUProfile',{'approved':True,'nodeName':a.node,'gpuUUID':'GPU-'+str(uuid.uuid4()),
        'cores':100,'memoryMiB':1024,'maxClients':2,'workerImage':image,'hamiNamespace':'hami-system','schedulerName':'hami-scheduler'}))
    request=create(resource('FlytGPURequest',{'vmRef':ref(vm),'profileRef':ref(profile),'count':1,'compute':10,'memory':'256Mi'}))
    channel=resource('FlytSharedMemoryChannel',{'vmRef':ref(vm),'requestRef':ref(request),'pvcRef':ref(pvc),
        'sessions':1,'uid':65532,'gid':65532,'image':image,'workerImage':image,'hookImage':image})
    rejected=json.loads(json.dumps(channel));rejected['metadata']['name']+='-denied';rejected['spec']['workerImage']='example.invalid/unapproved@sha256:'+'b'*64
    result=kubectl('create','-f','-','-n',a.namespace,input=rejected,check=False)
    assert result.returncode and 'worker image not approved' in result.stderr,result.stderr
    passed('live TLS webhook rejects a semantically unapproved worker image')
    baseline_pods=json.loads(kubectl('get','pods','-n',a.namespace,'-o','json').stdout)
    vm_spec=get('vm',name)['spec']
    create(channel)
    def reviewed():
        obj=get('flytsharedmemorychannel',name)
        return obj if obj.get('status',{}).get('observedGeneration')==1 else None
    observed=wait(reviewed)
    conditions={c['type']:c for c in observed['status']['conditions']}
    assert observed['status']['phase']=='ReviewOnly'
    assert conditions['Accepted']['status']=='True'
    assert conditions['Ready']['status']=='False'
    assert conditions['DependenciesReady']['status']=='False'
    assert 'GPUUnavailable' in conditions['DependenciesReady']['message'],conditions
    assert not observed['metadata'].get('finalizers')
    assert not observed['status'].get('allocation')
    assert get('vm',name)['spec']==vm_spec
    assert get('vm',name).get('status',{}).get('created') is not True
    current_pods=json.loads(kubectl('get','pods','-n',a.namespace,'-o','json').stdout)
    assert {p['metadata']['uid'] for p in baseline_pods['items']}=={p['metadata']['uid'] for p in current_pods['items']}
    passed('GPUUnavailable reported without allocation, finalizer, VM mutation, or Worker creation')
    before=observed['metadata']['resourceVersion']
    time.sleep(5)
    assert get('flytsharedmemorychannel',name)['metadata']['resourceVersion']==before
    passed('unchanged review does not churn status')
    kubectl('rollout','restart','deployment/'+a.release+'-flyt-controller','-n',a.namespace)
    kubectl('rollout','status','deployment/'+a.release+'-flyt-controller','-n',a.namespace,'--timeout=90s')
    assert get('flytsharedmemorychannel',name)['status']['phase']=='ReviewOnly'
    passed('controller restart preserves review status')
    kubectl('patch','flytsharedmemorychannel',name,'-n',a.namespace,'--type=merge','-p','{"spec":{"drain":true}}')
    wait(lambda:get('flytsharedmemorychannel',name)['status']['observedGeneration']==2)
    assert get('vm',name)['spec']==vm_spec
    passed('spec change observed after restart without executing drain in review mode')
    kubectl('delete','flytsharedmemorychannel',name,'-n',a.namespace,'--wait=true','--timeout=30s')
    created=[x for x in created if x[0]!='FlytSharedMemoryChannel']
    passed('review Channel deletes without stuck finalizers')
finally:
    for kind,n,cluster in reversed(created):
        args=['delete',kind,n,'--ignore-not-found','--wait=true','--timeout=30s']
        if not cluster:args+=['-n',a.namespace]
        kubectl(*args,check=False)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps({'namespace':a.namespace,'checks':results},indent=2)+'\n')
