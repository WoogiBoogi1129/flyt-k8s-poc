"""Small namespaced Kubernetes REST client; projected token reread per request."""
import json
import os
from pathlib import Path
import ssl
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from settings import namespace, positive_float


class DependencyNotReady(Exception):
    """An absent dependency is a waiting condition, not session failure."""

class API:
    def __init__(self):
        self.namespace=namespace()
        self.base=os.getenv('FLYT_API_URL','https://kubernetes.default.svc').rstrip('/')
        self.timeout=positive_float('FLYT_API_TIMEOUT_SECONDS',10)
        self.tls=ssl.create_default_context(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
    def path(self,kind,name=''):
        groups={'channels':('flyt.dev/v1alpha1','flytsharedmemorychannels'),
                'attachments':('flyt.dev/v1alpha1','flytchannelattachments'),
                'requests':('flyt.dev/v1alpha1','flytgpurequests'),
                'profiles':('flyt.dev/v1alpha1','flytgpuprofiles'),
                'vms':('kubevirt.io/v1','virtualmachines'),
                'vmis':('kubevirt.io/v1','virtualmachineinstances')}
        if kind in ('roles','rolebindings'):groups[kind]=('rbac.authorization.k8s.io/v1',kind)
        if kind in groups:
            group,plural=groups[kind];root='/apis/'+group
        else: root='/api/v1';plural=kind
        return root+'/namespaces/'+quote(self.namespace,safe='')+'/'+plural+('/'+quote(name,safe='') if name else '')
    def call(self,method,path,data=None):
        token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
        req=Request(self.base+path,method=method,data=None if data is None else json.dumps(data).encode(),
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/merge-patch+json' if method=='PATCH' else 'application/json'})
        try:
            with urlopen(req,context=self.tls,timeout=self.timeout) as f:return json.load(f)
        except HTTPError as e:
            if method=='GET' and e.code==404:return None
            raise
    def get(self,k,n):return self.call('GET',self.path(k,n))
    def items(self,k):
        result=self.call('GET',self.path(k))
        if result is None:raise DependencyNotReady(k+' API is not installed')
        return result['items']
    def create(self,k,o):return self.call('POST',self.path(k),o)
    def replace(self,k,o,status=False):return self.call('PUT',self.path(k,o['metadata']['name'])+('/status' if status else ''),o)
    def patch_status(self,k,o,values):
        # RV precondition prevents applying a decision made against an old spec.
        path=self.path(k,o['metadata']['name'])+'/status'
        data={'metadata':{'resourceVersion':o['metadata']['resourceVersion']},'status':values}
        return self.call('PATCH',path,data)
    def delete(self,k,o):return self.call('DELETE',self.path(k,o['metadata']['name']),
        {'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':o['metadata']['uid'],'resourceVersion':o['metadata']['resourceVersion']}})

def ref(o):return {'name':o['metadata']['name'],'uid':o['metadata']['uid']}
def require_ref(api,kind,r):
    o=api.get(kind,r['name'])
    if not o:raise DependencyNotReady(kind+' '+r['name']+' is unavailable')
    if o['metadata']['uid']!=r['uid'] or o['metadata'].get('deletionTimestamp'):
        raise ValueError(kind+' identity unavailable')
    return o
