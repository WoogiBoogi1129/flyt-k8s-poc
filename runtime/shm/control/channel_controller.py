"""Separate SHM-only controller. One replica (Recreate); optimistic RV updates.
No legacy Worker, Manager, RPC service or GPU mode mutation.
"""
import copy
import json
import os
import secrets
import time
from kube import API, ref, require_ref

FINAL='flyt.dev/shm-detach'
def owned(o,c):return any(r['uid']==c['metadata']['uid'] for r in o['metadata'].get('ownerReferences',[]))
def meta(c,name):return {'name':name,'namespace':c['metadata']['namespace'],
    'labels':{'flyt.dev/shm-channel':c['metadata']['name']},
    'ownerReferences':[{'apiVersion':c['apiVersion'],'kind':c['kind'],**ref(c),'controller':True,'blockOwnerDeletion':True}]}
def ensure(api,kind,obj,c):
    old=api.get(kind,obj['metadata']['name'])
    if old:
        if not owned(old,c):raise ValueError('same-name foreign object')
        return old
    return api.create(kind,obj)
def pod(c,name,image,command):
    return {'apiVersion':'v1','kind':'Pod','metadata':meta(c,name),'spec':{
        'restartPolicy':'Never','automountServiceAccountToken':False,
        'nodeSelector':{'kubernetes.io/hostname':c['status']['nodeName']},
        'securityContext':{'runAsUser':c['spec']['uid'],'runAsGroup':c['spec']['gid'],'fsGroup':c['spec']['gid']},
        'containers':[{'name':'main','image':image,'command':command,
            'volumeMounts':[{'name':'channel','mountPath':'/flyt-channel'}]}],
        'volumes':[{'name':'channel','persistentVolumeClaim':{'claimName':c['spec']['pvcRef']['name']}}]}}
def update(api,c,**values):
    c.setdefault('status',{}).update(values);api.replace('channels',c,True)

def reconcile(api,c):
    name=c['metadata']['name'];s=c.setdefault('status',{});spec=c['spec']
    if FINAL not in c['metadata'].get('finalizers',[]):
        if c['metadata'].get('deletionTimestamp'):return
        c['metadata'].setdefault('finalizers',[]).append(FINAL);api.replace('channels',c);return
    if c['metadata'].get('deletionTimestamp') or spec.get('drain',False) or s.get('phase') in ('Failed','Draining','Released'):
        # Never infer detach from missing Pods/API objects. Attachment observer must
        # retain terminal UID evidence or positively fence the node out-of-band.
        attachments=[a for a in api.items('attachments') if a['spec']['channelRef']==ref(c)]
        vm=api.get('vms',spec['vmRef']['name'])
        if vm and vm['metadata']['uid']==spec['vmRef']['uid']:
            if vm['spec'].get('runStrategy')!='Halted':
                vm['spec'].pop('running',None);vm['spec']['runStrategy']='Halted';api.replace('vms',vm)
        for p in api.items('pods'):
            if owned(p,c) and p['metadata']['name'].endswith('-worker') and not p['metadata'].get('deletionTimestamp'):
                api.delete('pods',p)
        complete=(s.get('everBound') and len(attachments)>=2 and all(
            a.get('status',{}).get('phase')=='Detached' and
            a['status'].get('observedGeneration')==a['metadata']['generation'] and
            a['spec']['generation']==s.get('generation') for a in attachments))
        # Even pre-bind failed allocation needs explicit backing detach evidence.
        if not complete:update(api,c,phase='Draining',reason='AwaitingDetachEvidence');return
        update(api,c,phase='Released',reason='DetachConfirmed')
        if c['metadata'].get('deletionTimestamp'):
            fresh=api.get('channels',name);fresh['metadata']['finalizers'].remove(FINAL);api.replace('channels',fresh)
        return
    vm=require_ref(api,'vms',spec['vmRef']);q=require_ref(api,'requests',spec['requestRef'])
    p=require_ref(api,'profiles',q['spec']['profileRef']);pvc=require_ref(api,'persistentvolumeclaims',spec['pvcRef'])
    if q['spec']['vmRef']!=spec['vmRef'] or not p['spec']['approved']:raise ValueError('request/profile not approved')
    if q['spec']['count']!=1 or not 1<=q['spec']['compute']<=p['spec']['cores']:raise ValueError('quota invalid')
    # Accept explicit Mi/Gi request only; frozen in status for this allocation.
    memory=q['spec']['memory'];unit=1024 if memory.endswith('Gi') else 1
    if not memory.endswith(('Mi','Gi')) or not memory[:-2].isdigit():raise ValueError('integer Mi/Gi memory required')
    mib=int(memory[:-2])*unit
    if not 1<=mib<=p['spec']['memoryMiB']:raise ValueError('memory exceeds profile')
    if pvc['spec'].get('volumeMode','Filesystem')!='Filesystem':raise ValueError('filesystem PVC required')
    if not s.get('allocation'):
        if api.get('vmis',vm['metadata']['name']):raise ValueError('stop existing VMI before allocating SHM')
        if vm['spec'].get('runStrategy')!='Halted':raise ValueError('VM must initially be Halted')
        update(api,c,phase='Reserved',allocation=secrets.token_hex(16),generation=secrets.token_hex(16),
            sessions=[secrets.token_hex(16) for _ in range(spec['sessions'])],nodeName=p['spec']['nodeName'],
            gpuUUID=p['spec']['gpuUUID'],memoryMiB=mib,compute=q['spec']['compute'],
            requestGeneration=q['metadata']['generation'],profileGeneration=p['metadata']['generation']);return
    if s['requestGeneration']!=q['metadata']['generation'] or s['profileGeneration']!=p['metadata']['generation']:
        raise ValueError('frozen request/profile changed; drain allocation')
    args=['python3','/opt/flyt/control/provision.py','--root','/flyt-channel','--allocation',s['allocation'],
          '--generation',s['generation'],'--uid',str(spec['uid']),'--gid',str(spec['gid'])]
    for sid in s['sessions']:args+=['--session',sid]
    prep=ensure(api,'pods',pod(c,name+'-prepare',spec['image'],args),c)
    if prep.get('status',{}).get('phase')!='Succeeded':
        if prep.get('status',{}).get('phase')=='Failed':raise ValueError('provision failed; allocation cannot be reused')
        return
    ann=vm['spec'].setdefault('template',{}).setdefault('metadata',{}).setdefault('annotations',{})
    if not ann.get('flyt.dev/shm-channel'):
        ann.update({'flyt.dev/shm-channel':name,'flyt.dev/shm-channel-uid':c['metadata']['uid'],
            'flyt.dev/shm-allocation':s['allocation'],'flyt.dev/shm-generation':s['generation'],
            'hooks.kubevirt.io/hookSidecars':json.dumps([{'image':spec['hookImage'],'args':['--version','v1alpha2'],
                'pvc':{'name':spec['pvcRef']['name'],'volumePath':'/flyt-channel','sharedComputePath':'/var/run/flyt-channel'}}])})
        v=vm['spec']['template'].setdefault('spec',{});v.setdefault('nodeSelector',{})['kubernetes.io/hostname']=s['nodeName']
        # Prevent legacy automatic Worker creation for this dedicated SHM VM.
        for key in ('flyt.dev/gpu-request','flyt.dev/profile','flyt.dev/control-plane'):ann.pop(key,None)
        api.replace('vms',vm);return
    if ann.get('flyt.dev/shm-channel-uid')!=c['metadata']['uid']:raise ValueError('VM already bound elsewhere')
    vmi=api.get('vmis',vm['metadata']['name'])
    if not vmi:
        if s.get('vmiUID'):update(api,c,phase='Draining',reason='VMIEnded');return
        update(api,c,phase='BackingReady',reason='ManualVMStartAllowed');return
    owners=vmi['metadata'].get('ownerReferences',[])
    if not any(x['uid']==vm['metadata']['uid'] for x in owners):raise ValueError('VMI owner mismatch')
    if s.get('vmiUID') and s['vmiUID']!=vmi['metadata']['uid']:raise ValueError('VMI generation changed')
    if vmi.get('status',{}).get('nodeName') not in (None,'',s['nodeName']):raise ValueError('VMI placed on wrong node')
    if not s.get('vmiUID'):update(api,c,phase='Bound',vmiUID=vmi['metadata']['uid'],everBound=True);return
    worker=pod(c,name+'-worker',spec['workerImage'],['python3','/opt/flyt/control/supervisor.py'])
    worker['spec']['schedulerName']=p['spec']['schedulerName']
    if p['spec'].get('runtimeClass'):worker['spec']['runtimeClassName']=p['spec']['runtimeClass']
    worker['metadata'].setdefault('annotations',{})['nvidia.com/use-gpuuuid']=s['gpuUUID']
    container=worker['spec']['containers'][0]
    quota={'nvidia.com/gpu':1,'nvidia.com/gpumem':s['memoryMiB'],'nvidia.com/gpucores':s['compute']}
    container['resources']={'requests':quota,'limits':quota}
    container['env']=[{'name':k,'value':str(v)} for k,v in {
        'FLYT_ALLOCATION':s['allocation'],'FLYT_GPU_UUID':s['gpuUUID'],'FLYT_RESOURCE_BACKEND':'hami',
        'FLYT_MEMORY_BYTES':s['memoryMiB']*1048576,'FLYT_SESSIONS':len(s['sessions'])}.items()]
    container['readinessProbe']={'exec':{'command':['test','-f','/tmp/flyt-worker-ready']},'periodSeconds':2}
    w=ensure(api,'pods',worker,c)
    # Bound identities are immutable; a restarted/removed Worker requires a new channel.
    if s.get('workerPodUID') and s['workerPodUID']!=w['metadata']['uid']:raise ValueError('worker generation changed')
    for role,holder in [('worker',w['metadata']['uid']),('guest',vmi['metadata']['uid'])]:
        ensure(api,'attachments',{'apiVersion':'flyt.dev/v1alpha1','kind':'FlytChannelAttachment',
            'metadata':meta(c,name+'-'+role),'spec':{'channelRef':ref(c),'generation':s['generation'],
            'role':role,'holderUID':holder,'nodeName':s['nodeName']}},c)
    attachments=[a for a in api.items('attachments') if a['spec']['channelRef']==ref(c)]
    ready=len(attachments)==2 and all(a.get('status',{}).get('phase')=='Mapped' and
        a['status'].get('observedGeneration')==a['metadata']['generation'] for a in attachments)
    update(api,c,phase='Ready' if ready else 'Bound',workerPodUID=w['metadata']['uid'],reason='MappingACK' if ready else 'AwaitingMappingACK')

if __name__=='__main__':
    api=API()
    while True:
        for channel in api.items('channels'):
            try:reconcile(api,channel)
            except Exception as e:
                # Do not continue unsafe execution after dependency or identity failures.
                print(channel['metadata']['name'],type(e).__name__,str(e),flush=True)
                try:update(api,channel,phase='Failed',reason=type(e).__name__)
                except Exception:pass
        time.sleep(2)
