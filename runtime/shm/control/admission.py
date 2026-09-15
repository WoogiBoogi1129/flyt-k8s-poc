"""Fail-closed VMI start/migration validation for a dedicated SHM namespace."""
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from kube import API

def validate(api,request):
    o=request['object'];kind=request['kind']['kind']
    if kind=='VirtualMachineInstanceMigration':
        v=api.get('vmis',o['spec']['vmiName'])
        if v and v['metadata'].get('annotations',{}).get('flyt.dev/shm-channel'):
            raise ValueError('active SHM migration unsupported')
        return
    a=o['metadata'].get('annotations',{});name=a.get('flyt.dev/shm-channel')
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
    vm=api.get('vms',c['spec']['vmRef']['name'])
    expected=vm['spec']['template']['metadata']['annotations']['hooks.kubevirt.io/hookSidecars']
    if a.get('hooks.kubevirt.io/hookSidecars')!=expected:raise ValueError('hook configuration changed')

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
