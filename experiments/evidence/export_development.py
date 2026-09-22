#!/usr/bin/env python3
"""Export a strict allowlist of development results, never the workspace tree."""
import argparse
import hashlib
import json
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);rows=[]
for path in sorted(a.source.rglob('metrics.json')):
    # Restrict to the current VM development runners, including fault parents.
    if not (path.parent/'timeline.json').exists() and not path.parent.parent.name.startswith('fault-'):continue
    metrics=json.loads(path.read_text());relative=path.parent.relative_to(a.source)
    target=a.output/relative;target.mkdir(parents=True,exist_ok=True)
    hashes={}
    for name in ['metrics.json','timeline.json','guest-stdout.txt','guest-stderr.txt']:
        original=path.parent/name
        if original.is_file() and not original.is_symlink():
            data=original.read_bytes();hashes[name]=hashlib.sha256(data).hexdigest();(target/name).write_bytes(data)
    # Binding and image identity fields only; no full Pod, VM/cloud-init, env,
    # SSH known_hosts, private keys, TLS material or Secret objects are copied.
    identity={}
    for name in ['channel-before.json','channel-bound.json','channel-ready.json','channel-released.json']:
        original=path.parent/name
        if original.is_file() and not original.is_symlink():
            value=json.loads(original.read_text());status=value.get('status',{})
            identity[name]={'uid':value['metadata']['uid'],
                'images':{k:value['spec'].get(k) for k in ['image','workerImage','hookImage']},
                'status':{k:status.get(k) for k in ['phase','allocation','generation','gpuUUID','memoryMiB','compute','vmiUID','workerPodUID','reason']}}
    if identity:(target/'identity.json').write_text(json.dumps(identity,indent=2))
    original=path.parent/'manifest.json'
    if original.is_file() and not original.is_symlink():
        manifest=json.loads(original.read_text())
        hashes['private_manifest.json']=hashlib.sha256(original.read_bytes()).hexdigest()
        (target/'program-hashes.json').write_text(json.dumps(manifest.get('file_sha256',{}),indent=2))
    rows.append({'case':str(relative),'status':metrics.get('status'),'scope':metrics.get('scope',metrics.get('mode')),'sha256':hashes})
(a.output/'index.json').write_text(json.dumps({'phase':'development','formal_training_completed':False,'cases':rows},indent=2))
print('Exported',len(rows),'case records; only allowlisted files copied')
