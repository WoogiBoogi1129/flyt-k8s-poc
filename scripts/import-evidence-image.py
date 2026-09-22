#!/usr/bin/env python3
"""Import one locally built OCI archive into gpu-4's CRI-O image storage.

This is a node-administration operation: a temporary privileged Pod executes only
the host podman image import. It does not restart CRI-O or modify GPU bindings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('archive',type=Path);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();archive=a.archive.resolve(strict=True)
if ROOT/'.local' not in archive.parents or archive.suffix!='.oci':p.error('expected this repository .local/*.oci archive')
a.output.mkdir(parents=True,exist_ok=False)
with tarfile.open(archive) as bundle:
 index=json.load(bundle.extractfile('index.json'))
 manifests=index.get('manifests',[])
 if len(manifests)!=1:raise ValueError('expected a single-image OCI archive')
 digest=manifests[0]['digest']
 if not digest.startswith('sha256:') or len(digest)!=71:raise ValueError('invalid OCI digest')
 manifest=bundle.extractfile('blobs/sha256/'+digest.split(':')[1]).read()
 if 'sha256:'+hashlib.sha256(manifest).hexdigest()!=digest:raise ValueError('OCI manifest digest mismatch')
(a.output/'archive-identity.json').write_text(json.dumps({'manifest_digest':digest,
 'reference':manifests[0].get('annotations',{}).get('org.opencontainers.image.ref.name')},indent=2))
print('OCI archive manifest digest:',digest,flush=True)
name='image-import-'+uuid.uuid4().hex[:10]
pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'flyt-evidence','labels':{'app.kubernetes.io/part-of':'flyt-evidence'}},
 'spec':{'nodeName':'gpu-4','restartPolicy':'Never','activeDeadlineSeconds':600,'automountServiceAccountToken':False,
 'containers':[{'name':'import','image':'docker.io/nvidia/cuda:12.8.1-base-ubuntu22.04@sha256:001469ea0f3dec85a1ca929aeea3b58ae369d4c11228b10aec1f642bb6ca7a6f',
 'command':['chroot','/host','/usr/bin/podman','load','--input',str(archive)],
 'securityContext':{'privileged':True,'runAsUser':0},'volumeMounts':[{'name':'host','mountPath':'/host'}]}],
 'volumes':[{'name':'host','hostPath':{'path':'/','type':'Directory'}}]}}
def run(args,**kw):return subprocess.check_output(['kubectl',*args],text=True,**kw)
(a.output/'manifest.json').write_text(json.dumps(pod,indent=2))
created=json.loads(run(['create','-f','-','-o','json'],input=json.dumps(pod)))
try:
 deadline=time.monotonic()+600
 while time.monotonic()<deadline:
  actual=json.loads(run(['get','pod',name,'-n','flyt-evidence','-o','json']))
  if actual['status']['phase'] in ('Succeeded','Failed'):break
  time.sleep(2)
 (a.output/'pod.json').write_text(json.dumps(actual,indent=2))
 (a.output/'log.txt').write_text(run(['logs',name,'-n','flyt-evidence']))
 if actual['status']['phase']!='Succeeded':raise RuntimeError('image import failed; see log')
 print((a.output/'log.txt').read_text())
finally:
 current=json.loads(run(['get','pod',name,'-n','flyt-evidence','-o','json']))
 if current['metadata']['uid']==created['metadata']['uid']:
  run(['delete','pod',name,'-n','flyt-evidence','--wait=true','--timeout=60s'])
