"""Attachment evidence helpers. Missing objects are NEVER detach evidence."""
import os
from kube import ref

DETACH_FINALIZER='flyt.dev/detach-observation'
CHANNEL_ANNOTATION='flyt.dev/detach-channel-uid'

def protect_pod(api,pod,c):
    """Keep the API object until detach evidence is durable, not the process alive."""
    meta=pod['metadata'];owner=meta.get('annotations',{}).get(CHANNEL_ANNOTATION)
    if owner not in (None,c['metadata']['uid']):raise ValueError('foreign detach observer')
    if DETACH_FINALIZER in meta.get('finalizers',[]):
        if owner!=c['metadata']['uid']:raise ValueError('unowned detach finalizer')
        return True
    if meta.get('deletionTimestamp'):return False  # Cannot add finalizers after deletion began.
    meta.setdefault('annotations',{})[CHANNEL_ANNOTATION]=c['metadata']['uid']
    meta.setdefault('finalizers',[]).append(DETACH_FINALIZER)
    api.replace('pods',pod)
    return True

def release_pod(api,pod,c):
    meta=pod['metadata']
    if DETACH_FINALIZER not in meta.get('finalizers',[]):return
    if meta.get('annotations',{}).get(CHANNEL_ANNOTATION)!=c['metadata']['uid']:
        raise ValueError('foreign detach observer')
    meta['finalizers'].remove(DETACH_FINALIZER)
    api.replace('pods',pod)

def terminal_pod(pod):
    if pod.get('status',{}).get('phase') not in ('Succeeded','Failed'):return False
    for spec_key,status_key in [('containers','containerStatuses'),('initContainers','initContainerStatuses'),
                                ('ephemeralContainers','ephemeralContainerStatuses')]:
        expected={x['name'] for x in pod['spec'].get(spec_key,[])}
        statuses=pod.get('status',{}).get(status_key,[])
        if {x['name'] for x in statuses}!=expected:return False
        if not all('terminated' in x.get('state',{}) for x in statuses):return False
    return bool(pod['spec'].get('containers'))

def terminal_unstarted_launcher(pod):
    """A deleted launcher stopped before its regular init barrier completed.

    Kubelet leaves never-created containers in PodInitializing, even in Failed.
    Require explicit sandbox teardown, full statuses and no main-container
    history; callers also require a Ready node and an attachment never Mapped.
    """
    state=pod.get('status',{});spec=pod['spec']
    if state.get('phase')!='Failed' or not pod['metadata'].get('deletionTimestamp'):return False
    conditions={x['type']:x for x in state.get('conditions',[])}
    if conditions.get('Initialized',{}).get('status')!='False':return False
    sandbox=conditions.get('PodReadyToStartContainers',{})
    if sandbox.get('status')!='False' or sandbox.get('reason')!='PodSandboxNotReady':return False
    if spec.get('ephemeralContainers') or state.get('ephemeralContainerStatuses'):return False
    def unstarted(x):
        return (x.get('state')=={'waiting':{'reason':'PodInitializing'}} and
                not x.get('containerID') and not x.get('imageID') and
                x.get('restartCount')==0 and x.get('started') is False and
                x.get('ready') is False and not x.get('lastState'))
    mains=state.get('containerStatuses',[]);inits=state.get('initContainerStatuses',[])
    for declared,observed in [(spec.get('containers',[]),mains),(spec.get('initContainers',[]),inits)]:
        if len(observed)!=len(declared) or {x['name'] for x in observed}!={x['name'] for x in declared}:return False
    if not mains or not all(unstarted(x) for x in mains):return False
    regular={x['name'] for x in spec.get('initContainers',[]) if x.get('restartPolicy')!='Always'}
    if not any(x['name'] in regular and unstarted(x) for x in inits):return False
    return all('terminated' in x.get('state',{}) or unstarted(x) for x in inits)

def node_ready(api,node):
    obj=api.call('GET','/api/v1/nodes/'+node)
    return bool(obj and any(x['type']=='Ready' and x['status']=='True' for x in obj.get('status',{}).get('conditions',[])))

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
    a.setdefault('status',{}).update({'phase':phase,'observedGeneration':a['metadata']['generation'],
        'reporterPodUID':os.environ['FLYT_POD_UID'],'evidence':'ChildProcessesReaped' if phase=='Detached' else 'QueueOpenAndGuard' if role=='worker' else 'GuestHELLOReceived'})
    api.replace('attachments',a,True)

def observe_guest(api,c):
    a=api.get('attachments',c['metadata']['name']+'-guest')
    if not a or a['spec']['channelRef']!=ref(c):return
    if a['spec']['generation']!=c.get('status',{}).get('generation'):return False
    uid=a['spec']['holderUID'];status=a.setdefault('status',{})
    pods=[p for p in api.items('pods') if p['metadata'].get('labels',{}).get('kubevirt.io/created-by')==uid]
    saved=status.get('launcherPodUID')
    if saved:pods=[p for p in pods if p['metadata']['uid']==saved]
    if len(pods)!=1:return
    pod=pods[0]
    if pod['spec'].get('nodeName')!=a['spec']['nodeName']:return
    if a.get('status',{}).get('phase')=='Detached':
        release_pod(api,pod,c);return False
    protected=protect_pod(api,pod,c)
    if not saved:
        status['launcherPodUID']=pod['metadata']['uid'];api.replace('attachments',a,True);return protected
    # Kubelet terminal phase with all containers terminated, on a Ready node.
    # Missing or force-deleted Pods cannot pass this condition.
    terminal=terminal_pod(pod)
    unstarted=status.get('phase') is None and terminal_unstarted_launcher(pod)
    if not (terminal or unstarted) or not node_ready(api,a['spec']['nodeName']):return protected
    status.update(phase='Detached',observedGeneration=a['metadata']['generation'],
        evidence='KubeletTerminalLauncher' if terminal else 'KubeletTerminalUnstartedLauncher',launcherPodUID=pod['metadata']['uid'])
    api.replace('attachments',a,True)
    release_pod(api,pod,c)
    return False

def observe_worker_exit(api,c):
    a=api.get('attachments',c['metadata']['name']+'-worker')
    if not a or a['spec']['channelRef']!=ref(c) or a['spec']['generation']!=c.get('status',{}).get('generation'):return
    p=api.get('pods',c['metadata']['name']+'-worker')
    if not p or p['metadata']['uid']!=a['spec']['holderUID']:return
    if a.get('status',{}).get('phase')=='Detached':
        release_pod(api,p,c);return
    protect_pod(api,p,c)
    if not terminal_pod(p) or not node_ready(api,a['spec']['nodeName']):return
    a['status']={'phase':'Detached','observedGeneration':a['metadata']['generation'],'evidence':'KubeletTerminalWorker','reporterPodUID':p['metadata']['uid']}
    api.replace('attachments',a,True)
    release_pod(api,p,c)
