#!/usr/bin/env python3
"""Compare CUDA imports in a PyTorch wheel with SHM guest exported symbols.

Static coverage only. An exported symbol can still reject a call; a missing
symbol may be supplied by a native library without forwarding it through SHM.
This is not a dynamic MLP trace and does not prove training compatibility.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--wheel',type=Path,required=True);p.add_argument('--guest-library',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def symbols(path,flag):
    return {line.split()[-1].split('@')[0] for line in subprocess.check_output(['nm','-D',flag,str(path)],text=True).splitlines() if line.split()}
exported=symbols(a.guest_library,'--defined-only');libraries={}
with tempfile.TemporaryDirectory() as temporary,zipfile.ZipFile(a.wheel) as wheel:
    for name in ['torch/lib/libc10_cuda.so','torch/lib/libtorch_cuda.so']:
        target=Path(temporary)/Path(name).name;target.write_bytes(wheel.read(name))
        imported={x for x in symbols(target,'--undefined-only') if re.match(r'(__cuda|cuda|cu[A-Z]|cublas|cudnn)',x)}
        libraries[target.name]={'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),
            'imported':sorted(imported),'missing_exports':sorted(imported-exported)}
result={'scope':'static CUDA imports; not a dynamic MLP trace or correctness result',
        'wheel_sha256':hashlib.file_digest(a.wheel.open('rb'),'sha256').hexdigest(),
        'guest_library_sha256':hashlib.file_digest(a.guest_library.open('rb'),'sha256').hexdigest(),
        'libraries':libraries}
with a.output.open('x') as output:json.dump(result,output,indent=2)
for name,info in libraries.items():print(name,len(info['imported']),'CUDA imports;',len(info['missing_exports']),'absent from SHM library')
