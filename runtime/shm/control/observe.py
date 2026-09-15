"""Attachment evidence helpers. Missing objects are NEVER detach evidence."""
import os
from kube import ref

def report(api,role,phase):
    name=os.environ['FLYT_CHANNEL_NAME'];c=api.get('channels',name)
    if not c or c['metadata']['uid']!=os.environ['FLYT_CHANNEL_UID']:raise ValueError('channel replaced')
    generation=os.environ['FLYT_GENERATION']
    if c.get('status',{}).get('generation')!=generation:raise ValueError('channel generation replaced')
    a=api.get('attachments',name+'-'+role)
    if not a or a['spec']['channelRef']!=ref(c) or a['spec']['generation']!=generation:raise ValueError('attachment unavailable')
    if role=='worker' and a['spec']['holderUID']!=os.environ['FLYT_POD_UID']:raise ValueError('worker replaced')
    if role=='guest' and phase!='Mapped':raise ValueError('Worker cannot attest QEMU detach')
    old=a.get('status',{}).get('phase')
    if old=='Detached':raise ValueError('terminal attachment')
    a['status']={'phase':phase,'observedGeneration':a['metadata']['generation'],
        'reporterPodUID':os.environ['FLYT_POD_UID'],'evidence':'ChildProcessesReaped' if phase=='Detached' else 'QueueOpenAndGuard' if role=='worker' else 'GuestHELLOReceived'}
    api.replace('attachments',a,True)

def observe_guest(api,c):
    a=api.get('attachments',c['metadata']['name']+'-guest')
    if not a or a['spec']['channelRef']!=ref(c):return
    if a.get('status',{}).get('phase')=='Detached':return
    uid=a['spec']['holderUID'];status=a.setdefault('status',{})
    pods=[p for p in api.items('pods') if p['metadata'].get('labels',{}).get('kubevirt.io/created-by')==uid]
    saved=status.get('launcherPodUID')
    if saved:pods=[p for p in pods if p['metadata']['uid']==saved]
    if len(pods)!=1:return
    pod=pods[0]
    if pod['spec'].get('nodeName')!=a['spec']['nodeName']:return
    if not saved:
        status['launcherPodUID']=pod['metadata']['uid'];api.replace('attachments',a,True);return
    # Kubelet terminal phase with all containers terminated, on a Ready node.
    # Missing or force-deleted Pods cannot pass this condition.
    statuses=pod.get('status',{}).get('containerStatuses',[])
    init=pod.get('status',{}).get('initContainerStatuses',[])
    if pod.get('status',{}).get('phase') not in ('Succeeded','Failed') or not statuses:return
    if not all('terminated' in x.get('state',{}) for x in statuses+init):return
    node=api.call('GET','/api/v1/nodes/'+a['spec']['nodeName'])
    if not node or not any(x['type']=='Ready' and x['status']=='True' for x in node.get('status',{}).get('conditions',[])):return
    status.update(phase='Detached',observedGeneration=a['metadata']['generation'],evidence='KubeletTerminalLauncher',launcherPodUID=pod['metadata']['uid'])
    api.replace('attachments',a,True)

def observe_worker_exit(api,c):
    a=api.get('attachments',c['metadata']['name']+'-worker')
    if not a or a['spec']['channelRef']!=ref(c) or a.get('status',{}).get('phase')=='Detached':return
    p=api.get('pods',c['metadata']['name']+'-worker')
    if not p or p['metadata']['uid']!=a['spec']['holderUID']:return
    statuses=p.get('status',{}).get('containerStatuses',[])
    if p.get('status',{}).get('phase') not in ('Succeeded','Failed') or not statuses or not all('terminated' in x.get('state',{}) for x in statuses):return
    node=api.call('GET','/api/v1/nodes/'+a['spec']['nodeName'])
    if not node or not any(x['type']=='Ready' and x['status']=='True' for x in node.get('status',{}).get('conditions',[])):return
    a['status']={'phase':'Detached','observedGeneration':a['metadata']['generation'],'evidence':'KubeletTerminalWorker','reporterPodUID':p['metadata']['uid']}
    api.replace('attachments',a,True)
