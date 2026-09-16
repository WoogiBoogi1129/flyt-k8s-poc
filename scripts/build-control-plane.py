#!/usr/bin/env python3
"""Build the COPY-only CPU image without a privileged Docker daemon.

Requires crane. Mirrors ControlPlane.Containerfile; no CUDA, compiler, or RUN.
Produces a Docker image archive, which `crane push` can publish to GHCR.
"""
import argparse
import io
from pathlib import Path
import re
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--crane', default='crane')
p.add_argument('--output', type=Path, default=root / '.local/control-plane.tar')
a = p.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
recipe = (root / 'images/flyt/ControlPlane.Containerfile').read_text()
base = re.search(r'^FROM (\S+)', recipe).group(1)
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root): revision += '-dirty'
layer = a.output.parent / 'control-plane-layer.tar'
with tarfile.open(layer, 'w') as tar:
    for source in sorted((root / 'runtime/shm/control').glob('*.py')):
        data = source.read_bytes()
        info = tarfile.TarInfo('opt/flyt/control/' + source.name)
        info.size, info.mode, info.mtime = len(data), 0o644, 0
        tar.addfile(info, io.BytesIO(data))
subprocess.run([a.crane, 'mutate', '--platform', 'linux/amd64', base,
    '--append', str(layer), '--output', str(a.output),
    '--entrypoint', 'python3,/opt/flyt/control/control_plane.py', '--cmd=',
    '--user', '65532:65532', '--workdir', '/opt/flyt/control',
    '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONUNBUFFERED=1', '--env', 'FLYT_MODE=review',
    '--exposed-ports', '8080/tcp,8443/tcp',
    '--label', 'org.opencontainers.image.source=https://github.com/WoogiBoogi1129/flyt-k8s-poc',
    '--label', 'org.opencontainers.image.revision=' + revision], check=True)
print('Built', a.output, 'revision', revision)
