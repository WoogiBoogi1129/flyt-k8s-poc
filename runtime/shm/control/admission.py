"""Fail-closed VMI start/migration validation for a dedicated SHM namespace."""
import json
import os
import ssl
import signal
import threading
import hashlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from kube import API
from health import Health, log
from settings import mode, positive_float

def validate(api,request):
    o=request['object'];kind=request['kind']['kind']
    if kind=='FlytSharedMemoryChannel':
        # Schema/CEL handle shape and immutability. Check semantic consistency
        # when references exist; missing dependencies remain observable on CRs.
        q=api.get('requests',o['spec']['requestRef']['name'])
        if q and q['metadata']['uid']==o['spec']['requestRef']['uid']:
            if q['spec']['vmRef']!=o['spec']['vmRef']:raise ValueError('request references a different VM')
            p=api.get('profiles',q['spec']['profileRef']['name'])
            if p and p['metadata']['uid']==q['spec']['profileRef']['uid']:
                if not p['spec']['approved']:raise ValueError('profile is not approved')
                if o['spec']['workerImage']!=p['spec']['workerImage']:raise ValueError('worker image not approved')
                if o['spec']['sessions']>p['spec']['maxClients']:raise ValueError('session count exceeds profile')
        return
    if kind=='FlytChannelAttachment':
        spec=o['spec'];c=api.get('channels',spec['channelRef']['name']);status=o.get('status',{})
        if not c or c['metadata']['uid']!=spec['channelRef']['uid'] or c.get('status',{}).get('generation')!=spec['generation']:
            raise ValueError('stale attachment')
        actor=request['userInfo']['username'];prefix='system:serviceaccount:'+api.namespace+':'
        if actor==prefix+os.getenv('FLYT_CONTROLLER_SERVICE_ACCOUNT','flyt-shm-controller'):return
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
    def log_message(self,*_):pass
    def do_POST(self):
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=2**20:self.send_error(413);return
            body=json.loads(self.rfile.read(n));r=body['request']
            if not r.get('uid'):raise ValueError('AdmissionReview UID required')
        except (ValueError,KeyError,TypeError):self.send_error(400);return
        answer={'uid':r['uid'],'allowed':False}
        try:validate(API(),r);answer['allowed']=True
        except Exception as e:
            answer['status']={'message':str(e),'code':403}
            log('admission_denied',uid=r['uid'],error=type(e).__name__,message=str(e))
        data=json.dumps({'apiVersion':'admission.k8s.io/v1','kind':'AdmissionReview','response':answer}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
def main():
    mode()  # Validate configuration even though the chart selects webhook rules.
    health=Health(positive_float('FLYT_READY_STALE_SECONDS',60));stop=threading.Event()
    health_server=health.serve(int(os.getenv('FLYT_HEALTH_PORT','8080')))
    server=ThreadingHTTPServer(('0.0.0.0',int(os.getenv('FLYT_WEBHOOK_PORT','8443'))),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert=os.getenv('FLYT_TLS_CERT','/tls/tls.crt');key=os.getenv('FLYT_TLS_KEY','/tls/tls.key')
    tls.load_cert_chain(cert,key)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    def monitor():
        api=API()
        previous=None
        while not stop.is_set():
            try:
                fingerprint=hashlib.sha256(Path(cert).read_bytes()+Path(key).read_bytes()).digest()
                if fingerprint!=previous:
                    tls.load_cert_chain(cert,key)
                    previous=fingerprint
                    log('tls_loaded')
                api.items('channels');health.api_ok()
            except Exception as error:log('webhook_api_retry',error=type(error).__name__)
            stop.wait(5)
    def shutdown(*_):
        health.stopping=True;stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    threading.Thread(target=monitor,daemon=True).start()
    log('webhook_started',mode=mode())
    try:server.serve_forever()
    finally:health_server.shutdown();server.server_close()


if __name__=='__main__':main()
