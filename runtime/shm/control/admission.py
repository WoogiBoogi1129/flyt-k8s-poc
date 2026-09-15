"""Fail-closed VMI start/migration validation for a dedicated SHM namespace."""
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from kube import API

def validate(api,request):
    o=request['object'];kind=request['kind']['kind']
    if kind=='FlytChannelAttachment':
        spec=o['spec'];c=api.get('channels',spec['channelRef']['name']);status=o.get('status',{})
        if not c or c['metadata']['uid']!=spec['channelRef']['uid'] or c.get('status',{}).get('generation')!=spec['generation']:
            raise ValueError('stale attachment')
        actor=request['userInfo']['username'];prefix='system:serviceaccount:'+api.namespace+':'
        if actor==prefix+'flyt-shm-controller':return
        if actor!=prefix+c['metadata']['name']+'-worker':raise ValueError('unauthorized attachment reporter')
        pod=api.get('pods',c['metadata']['name']+'-worker')
        if not pod or pod['metadata']['uid']!=status.get('reporterPodUID'):raise ValueError('stale reporter Pod')
        if status.get('observedGeneration')!=o['metadata']['generation']:raise ValueError('stale observation')
        if spec['role']=='guest' and status.get('phase')!='Mapped':raise ValueError('Worker cannot attest QEMU detach')
        if spec['role']=='worker' and spec['holderUID']!=pod['metadata']['uid']:raise ValueError('wrong worker holder')
        if status.get('phase') not in ('Mapped','Detached'):raise ValueError('invalid phase')
        if request.get('oldObject',{}).get('status',{}).get('phase')=='Detached':raise ValueError('terminal attachment')
        return
    if kind=='VirtualMachineInstanceMigration':
        v=api.get('vmis',o['spec']['vmiName'])
        if v and v['metadata'].get('annotations',{}).get('flyt.dev/shm-channel'):
            raise ValueError('active SHM migration unsupported')
        return
    a=o['metadata'].get('annotations',{});name=a.get('flyt.dev/shm-channel')
    if request['operation']=='UPDATE':
        old=request['oldObject'];oa=old['metadata'].get('annotations',{})
        for key in ('flyt.dev/shm-channel','flyt.dev/shm-channel-uid','flyt.dev/shm-allocation','flyt.dev/shm-generation','hooks.kubevirt.io/hookSidecars'):
            if a.get(key)!=oa.get(key):raise ValueError('active VMI binding is immutable')
        if o['spec'].get('nodeSelector')!=old['spec'].get('nodeSelector'):raise ValueError('active placement immutable')
        return
    if not name:raise ValueError('SHM namespace requires channel binding')
    c=api.get('channels',name)
    if not c or c['metadata'].get('deletionTimestamp') or c['spec'].get('drain'):raise ValueError('channel unavailable')
    s=c.get('status',{})
    if s.get('phase')!='BackingReady' or s.get('vmiUID'):raise ValueError('channel not available for new VMI')
    if a.get('flyt.dev/shm-channel-uid')!=c['metadata']['uid'] or a.get('flyt.dev/shm-generation')!=s['generation'] or a.get('flyt.dev/shm-allocation')!=s['allocation']:
        raise ValueError('binding identity mismatch')
    if not any(x['uid']==c['spec']['vmRef']['uid'] for x in o['metadata'].get('ownerReferences',[])):
        raise ValueError('VM owner mismatch')
    if o['spec'].get('nodeSelector',{}).get('kubernetes.io/hostname')!=s['nodeName']:
        raise ValueError('co-placement required')
    expected=[{'image':c['spec']['hookImage'],'args':['--version','v1alpha2'],
        'pvc':{'name':c['spec']['pvcRef']['name'],'volumePath':'/flyt-channel','sharedComputePath':'/var/run/flyt-channel'}}]
    if json.loads(a.get('hooks.kubevirt.io/hookSidecars','null'))!=expected:raise ValueError('hook configuration changed')

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=2**20:self.send_error(413);return
        body=json.loads(self.rfile.read(n));r=body['request'];answer={'uid':r['uid'],'allowed':False}
        try:validate(API(),r);answer['allowed']=True
        except Exception as e:answer['status']={'message':str(e),'code':403}
        data=json.dumps({'apiVersion':'admission.k8s.io/v1','kind':'AdmissionReview','response':answer}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
if __name__=='__main__':
    server=HTTPServer(('0.0.0.0',8443),Handler);tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain('/tls/tls.crt','/tls/tls.key');server.socket=tls.wrap_socket(server.socket,server_side=True);server.serve_forever()
