#!/usr/bin/env python3
"""Explicit stage-one install: validate CRDs, deploy, then register admission.

Existing RPC/unknown CRDs are rejected. CRDs and TLS Secrets survive uninstall.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--namespace', required=True)
p.add_argument('--release', default='flyt')
p.add_argument('--image-digest', required=True)
p.add_argument('--tls-secret', default='flyt-admission-tls')
p.add_argument('--ca', type=Path, required=True)
p.add_argument('--values', action='append', type=Path, default=[])
p.add_argument('--timeout', default='5m')
a = p.parse_args()

def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)

# Validate Helm inputs before making any changes.
base = ['helm', 'upgrade', '--install', a.release, str(root/'charts/flyt-control-plane'),
        '-n', a.namespace, '--create-namespace', '--skip-crds', '--wait', '--timeout', a.timeout]
for f in a.values: base += ['-f', str(f)]
with tempfile.TemporaryDirectory(prefix='flyt-install-') as temp:
    values = Path(temp)/'tls.json'
    values.write_text(json.dumps({'image': {'digest': a.image_digest},
        'tls': {'existingSecret': a.tls_secret, 'caBundle': base64.b64encode(a.ca.read_bytes()).decode()}}))
    base += ['-f', str(values)]
    run('helm','template',a.release,str(root/'charts/flyt-control-plane'),'-n',a.namespace,
        *[x for f in a.values for x in ('-f',str(f))],'-f',str(values),stdout=subprocess.DEVNULL)
    run('kubectl','get','secret',a.tls_secret,'-n',a.namespace,stdout=subprocess.DEVNULL)
    for file in sorted((root/'charts/flyt-control-plane/crds').glob('*.json')):
        doc=json.loads(file.read_text())
        for crd in doc.get('items',[doc]):
            name=crd['metadata']['name']
            raw=subprocess.check_output(['kubectl','get','crd',name,'--ignore-not-found','-o','json'],text=True)
            if raw and json.loads(raw)['metadata'].get('labels',{}).get('flyt.dev/control-plane-api')!='shm-stage1':
                raise SystemExit('Refusing existing unowned/RPC CRD: '+name+'; use a separate cluster or review a migration')
        run('kubectl','apply','--dry-run=server','-f',str(file),stdout=subprocess.DEVNULL)
    for file in sorted((root/'charts/flyt-control-plane/crds').glob('*.json')):
        run('kubectl','apply','-f',str(file))
    # On an upgrade keep an existing webhook registered. Disabling it, even
    # briefly, would open an admission gap. Two-phase bootstrap is first-install only.
    exists=subprocess.run(['helm','status',a.release,'-n',a.namespace],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if not exists: run(*base,'--set','webhook.registrationEnabled=false')
    run(*base,'--set','webhook.registrationEnabled=true')
print('Installed',a.release,'in',a.namespace,'(review unless explicitly overridden in values)')
