#!/usr/bin/env python3
"""Historical experimental active layout; use charts/flyt-control-plane for review deployments."""
import argparse
import base64
import json
from pathlib import Path
import re

def render(namespace,image,ca):
    if not re.fullmatch('flyt-shm-[a-z0-9-]{1,40}',namespace):raise ValueError('dedicated flyt-shm-* namespace required')
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',image):raise ValueError('digest-pinned control image required')
    if not ca.startswith(b'-----BEGIN CERTIFICATE-----'):raise ValueError('PEM CA certificate required')
    meta=lambda name:{'name':name,'namespace':namespace}
    sa={'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('flyt-shm-controller')}
    rules=[{'apiGroups':['flyt.dev'],'resources':['flytsharedmemorychannels','flytsharedmemorychannels/status','flytchannelattachments','flytchannelattachments/status'],'verbs':['get','list','create','update','delete']},
        {'apiGroups':['flyt.dev'],'resources':['flytgpuprofiles','flytgpurequests'],'verbs':['get','list']},
        {'apiGroups':['kubevirt.io'],'resources':['virtualmachines'],'verbs':['get','list','update']},
        {'apiGroups':['kubevirt.io'],'resources':['virtualmachineinstances'],'verbs':['get','list']},
        {'apiGroups':[''],'resources':['pods','serviceaccounts'],'verbs':['get','list','create','delete']},
        {'apiGroups':[''],'resources':['pods'],'verbs':['update']},
        {'apiGroups':[''],'resources':['persistentvolumeclaims'],'verbs':['get']},
        {'apiGroups':['rbac.authorization.k8s.io'],'resources':['roles','rolebindings'],'verbs':['get','create']}]
    role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':meta('flyt-shm-controller'),'rules':rules}
    binding={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':meta('flyt-shm-controller'),
        'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'flyt-shm-controller'},
        'subjects':[{'kind':'ServiceAccount','name':'flyt-shm-controller','namespace':namespace}]}
    cluster={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':namespace+'-node-reader'},
        'rules':[{'apiGroups':[''],'resources':['nodes','persistentvolumes'],'verbs':['get']}]}
    clusterbinding={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':namespace+'-node-reader'},
        'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':namespace+'-node-reader'},'subjects':binding['subjects']}
    env=[{'name':'FLYT_NAMESPACE','value':namespace},{'name':'FLYT_MODE','value':'active'}]
    deployment={'apiVersion':'apps/v1','kind':'Deployment','metadata':meta('flyt-shm-controller'),'spec':{'replicas':1,'strategy':{'type':'Recreate'},
        'selector':{'matchLabels':{'app':'flyt-shm-controller'}},'template':{'metadata':{'labels':{'app':'flyt-shm-controller'}},'spec':{
            'serviceAccountName':'flyt-shm-controller','securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532},
            'containers':[{'name':'controller','image':image,'env':env,'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'memory':'512Mi'}}},
                {'name':'admission','image':image,'command':['python3','/opt/flyt/control/admission.py'],'env':env,
                 'ports':[{'name':'https','containerPort':8443}],
                 'readinessProbe':{'tcpSocket':{'port':8443},'periodSeconds':2},
                 'volumeMounts':[{'name':'tls','mountPath':'/tls','readOnly':True}]}],
            'volumes':[{'name':'tls','secret':{'secretName':'flyt-shm-admission-tls'}}]}}}}
    service={'apiVersion':'v1','kind':'Service','metadata':meta('flyt-shm-admission'),'spec':{
        'selector':{'app':'flyt-shm-controller'},'ports':[{'port':443,'targetPort':8443}]}}
    webhook={'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingWebhookConfiguration','metadata':{'name':namespace+'-start-gate'},'webhooks':[{
        'name':'start.shm.flyt.dev','admissionReviewVersions':['v1'],'sideEffects':'None','failurePolicy':'Fail','timeoutSeconds':10,
        'namespaceSelector':{'matchLabels':{'flyt.dev/shm':'true','kubernetes.io/metadata.name':namespace}},
        'clientConfig':{'service':{'namespace':namespace,'name':'flyt-shm-admission','path':'/'},'caBundle':base64.b64encode(ca).decode()},
        'rules':[{'apiGroups':['kubevirt.io'],'apiVersions':['v1'],'operations':['CREATE','UPDATE'],
                  'resources':['virtualmachineinstances','virtualmachineinstancemigrations'],'scope':'Namespaced'},
                 {'apiGroups':['flyt.dev'],'apiVersions':['v1alpha1'],'operations':['UPDATE'],
                  'resources':['flytchannelattachments/status'],'scope':'Namespaced'}]}]}
    return {'apiVersion':'v1','kind':'List','items':[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace,'labels':{'flyt.dev/shm':'true'}}},sa,role,binding,cluster,clusterbinding,deployment,service,webhook]}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--namespace',required=True);p.add_argument('--image',required=True);p.add_argument('--ca',type=Path,required=True)
    a=p.parse_args();print(json.dumps(render(a.namespace,a.image,a.ca.read_bytes()),indent=2))
