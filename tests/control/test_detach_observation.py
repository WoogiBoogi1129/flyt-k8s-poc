import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runtime/shm/control'))
from observe import observe_guest, observe_worker_exit, DETACH_FINALIZER, CHANNEL_ANNOTATION


class API:
    def __init__(self):
        self.objects={};self.writes=[];self.ready=True;self.fail_status=False
    def get(self,kind,name):return copy.deepcopy(self.objects.get((kind,name)))
    def items(self,kind):return [copy.deepcopy(v) for (k,_),v in self.objects.items() if k==kind]
    def call(self,*args):return {'status':{'conditions':[{'type':'Ready','status':'True' if self.ready else 'False'}]}}
    def replace(self,kind,obj,status=False):
        if self.fail_status and kind=='attachments':raise RuntimeError('API unavailable')
        self.writes.append((kind,copy.deepcopy(obj),status))
        self.objects[kind,obj['metadata']['name']]=copy.deepcopy(obj)


class DetachTests(unittest.TestCase):
    def setUp(self):
        self.api=API()
        self.channel={'metadata':{'name':'ch','uid':'channel'},'status':{'generation':'gen'}}
        self.pod={'metadata':{'name':'launcher','uid':'pod','labels':{'kubevirt.io/created-by':'vmi'}},
                  'spec':{'nodeName':'node','containers':[{'name':'compute'},{'name':'hook'}]},
                  'status':{'phase':'Running'}}
        self.attachment={'metadata':{'name':'ch-guest','generation':1},
                         'spec':{'channelRef':{'name':'ch','uid':'channel'},'generation':'gen','holderUID':'vmi','nodeName':'node'},
                         'status':{'phase':'Mapped'}}
        self.api.objects['pods','launcher']=self.pod
        self.api.objects['attachments','ch-guest']=self.attachment
    def terminal(self):
        p=self.api.objects['pods','launcher']
        p['metadata']['deletionTimestamp']='now'
        p['status']={'phase':'Succeeded','containerStatuses':[{'name':x,'state':{'terminated':{'exitCode':0}}} for x in ('compute','hook')]}
    def test_protect_before_ready_then_persist_evidence_before_unpin(self):
        self.assertTrue(observe_guest(self.api,self.channel))
        p=self.api.objects['pods','launcher']
        self.assertIn(DETACH_FINALIZER,p['metadata']['finalizers'])
        self.assertEqual(p['metadata']['annotations'][CHANNEL_ANNOTATION],'channel')
        self.terminal();self.api.writes.clear()
        observe_guest(self.api,self.channel)
        self.assertEqual(self.api.objects['attachments','ch-guest']['status']['phase'],'Detached')
        self.assertEqual([x[0] for x in self.api.writes],['attachments','pods'])
        self.assertNotIn(DETACH_FINALIZER,self.api.objects['pods','launcher']['metadata']['finalizers'])
    def test_restart_after_evidence_write_retries_finalizer_removal(self):
        observe_guest(self.api,self.channel);self.terminal()
        self.api.objects['attachments','ch-guest']['status']['phase']='Detached'
        observe_guest(self.api,self.channel)
        self.assertNotIn(DETACH_FINALIZER,self.api.objects['pods','launcher']['metadata']['finalizers'])
    def test_api_failure_preserves_protection(self):
        observe_guest(self.api,self.channel);self.terminal();self.api.fail_status=True
        with self.assertRaises(RuntimeError):observe_guest(self.api,self.channel)
        self.assertIn(DETACH_FINALIZER,self.api.objects['pods','launcher']['metadata']['finalizers'])
        self.assertEqual(self.api.objects['attachments','ch-guest']['status']['phase'],'Mapped')
    def test_missing_pod_not_detached(self):
        observe_guest(self.api,self.channel);del self.api.objects['pods','launcher']
        observe_guest(self.api,self.channel)
        self.assertEqual(self.api.objects['attachments','ch-guest']['status']['phase'],'Mapped')
    def test_incomplete_container_status_not_detached(self):
        observe_guest(self.api,self.channel);self.terminal()
        self.api.objects['pods','launcher']['status']['containerStatuses'].pop()
        observe_guest(self.api,self.channel)
        self.assertEqual(self.api.objects['attachments','ch-guest']['status']['phase'],'Mapped')
    def test_node_unreachable_not_detached(self):
        observe_guest(self.api,self.channel);self.terminal();self.api.ready=False
        observe_guest(self.api,self.channel)
        self.assertEqual(self.api.objects['attachments','ch-guest']['status']['phase'],'Mapped')
    def test_cannot_protect_already_deleting_pod(self):
        self.api.objects['pods','launcher']['metadata']['deletionTimestamp']='now'
        self.assertFalse(observe_guest(self.api,self.channel))
        self.assertNotIn('finalizers',self.api.objects['pods','launcher']['metadata'])
    def test_stale_generation_is_not_mutated(self):
        self.api.objects['attachments','ch-guest']['spec']['generation']='old'
        self.assertFalse(observe_guest(self.api,self.channel));self.assertEqual(self.api.writes,[])
    def test_worker_report_releases_only_its_pod(self):
        a=copy.deepcopy(self.attachment);a['metadata']['name']='ch-worker';a['spec']['holderUID']='worker';a['status']['phase']='Detached'
        p=copy.deepcopy(self.pod);p['metadata'].update(name='ch-worker',uid='worker',finalizers=[DETACH_FINALIZER],annotations={CHANNEL_ANNOTATION:'channel'})
        self.api.objects['attachments','ch-worker']=a;self.api.objects['pods','ch-worker']=p
        observe_worker_exit(self.api,self.channel)
        self.assertNotIn(DETACH_FINALIZER,self.api.objects['pods','ch-worker']['metadata']['finalizers'])
        self.assertEqual(self.api.objects['pods','launcher'],self.pod)

    def unstarted_launcher(self):
        self.api.objects['attachments','ch-guest']['status']={}
        observe_guest(self.api,self.channel)
        p=self.api.objects['pods','launcher'];p['metadata']['deletionTimestamp']='now'
        p['spec']['initContainers']=[{'name':'log','restartPolicy':'Always'},{'name':'disk'}]
        def waiting(name):return {'name':name,'state':{'waiting':{'reason':'PodInitializing'}},
            'imageID':'','restartCount':0,'started':False,'ready':False,'lastState':{}}
        p['status']={'phase':'Failed','conditions':[{'type':'Initialized','status':'False'},
            {'type':'PodReadyToStartContainers','status':'False','reason':'PodSandboxNotReady'}],
            'containerStatuses':[waiting('compute'),waiting('hook')],
            'initContainerStatuses':[{'name':'log','state':{'terminated':{'exitCode':0}}},waiting('disk')]}
        return p

    def test_cancelled_init_failure_persists_proof_before_release(self):
        self.unstarted_launcher();self.api.writes.clear()
        observe_guest(self.api,self.channel)
        status=self.api.objects['attachments','ch-guest']['status']
        self.assertEqual(status['phase'],'Detached')
        self.assertEqual(status['evidence'],'KubeletTerminalUnstartedLauncher')
        self.assertEqual([x[0] for x in self.api.writes],['attachments','pods'])

    def test_unstarted_rejects_uncertain_or_live_container(self):
        mutations=[lambda p:p['status'].update(phase='Pending'),
            lambda p:p['metadata'].pop('deletionTimestamp'),
            lambda p:p['status'].update(conditions=[]),
            lambda p:p['status']['containerStatuses'].pop(),
            lambda p:p['status']['containerStatuses'][0].update(containerID='cri-o://old'),
            lambda p:p['status']['containerStatuses'][0].update(restartCount=1),
            lambda p:p['status']['containerStatuses'][0].update(lastState={'terminated':{}}),
            lambda p:p['status']['containerStatuses'][0].update(state={'waiting':{'reason':'ContainerStatusUnknown'}}),
            lambda p:p['status']['initContainerStatuses'][0].update(state={'running':{}}),
            lambda p:p['status']['initContainerStatuses'].pop(),
            lambda p:p['spec'].update(ephemeralContainers=[{'name':'debug'}])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.setUp();p=self.unstarted_launcher();mutate(p)
                observe_guest(self.api,self.channel)
                self.assertNotEqual(self.api.objects['attachments','ch-guest']['status'].get('phase'),'Detached')
                self.assertIn(DETACH_FINALIZER,p['metadata']['finalizers'])

    def test_unstarted_does_not_override_prior_mapping_or_unready_node(self):
        for mapped in [False,True]:
            with self.subTest(mapped=mapped):
                self.setUp();self.unstarted_launcher()
                if mapped:self.api.objects['attachments','ch-guest']['status']['phase']='Mapped'
                else:self.api.ready=False
                observe_guest(self.api,self.channel)
                self.assertNotEqual(self.api.objects['attachments','ch-guest']['status'].get('phase'),'Detached')


if __name__=='__main__':unittest.main()
