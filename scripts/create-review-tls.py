#!/usr/bin/env python3
"""Generate a lab CA and webhook certificate locally. Never prints private keys.

For managed environments, use an existing CA/Secret or cert-manager instead.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--namespace', required=True)
p.add_argument('--release', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
for name in (a.namespace, a.release):
    if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?', name): p.error('invalid namespace/release')
if a.output.exists(): p.error('output directory already exists; use a new directory for rotation')
a.output.mkdir(parents=True, mode=0o700)
os.umask(0o077)
service = (a.release + '-flyt')[:48].rstrip('-') + '-webhook'
san = f'{service}.{a.namespace}.svc'
def openssl(*args):
    subprocess.run(['openssl', *args], cwd=a.output, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365', '-keyout', 'ca.key', '-out', 'ca.crt',
        '-subj', '/CN=FLYT lab CA', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'tls.key', '-out', 'tls.csr', '-subj', '/CN=' + san)
(a.output / 'extensions.cnf').write_text('subjectAltName=DNS:' + san + '\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
openssl('x509', '-req', '-in', 'tls.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'tls.crt',
        '-days', '90', '-extfile', 'extensions.cnf')
print('Generated certificate for', san, 'under', a.output)
