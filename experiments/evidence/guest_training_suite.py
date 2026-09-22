#!/usr/bin/env python3
"""Development suite in one CUDA session; hold results until host collection.

Each invocation of train.py resets the model/optimizer from its fixture. This
is not a replacement for the independent processes required by E3 performance.
"""
import argparse
import json
from pathlib import Path
import runpy
import sys
import time
import traceback

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--spec',required=True,type=Path)
p.add_argument('--out',required=True,type=Path)
p.add_argument('--hold-seconds',type=int,default=90)
a=p.parse_args()
if not 0<=a.hold_seconds<=300:p.error('hold must be 0..300 seconds')
a.out.mkdir(exist_ok=False,parents=True)
spec=json.loads(a.spec.read_text());rows=[];failed=False
for case in spec['cases']:
    target=a.out/case['id']
    if target.parent!=a.out:raise ValueError('case ID must be a plain name')
    if failed:
        rows.append({'id':case['id'],'status':'NOT_RUN'});continue
    sys.argv=[str(Path(__file__).with_name('train.py')),'run','--fixture',case['fixture'],
              '--backend',spec['backend'],'--mode','correctness','--batch',str(case['batch']),
              '--input-mode',case['input_mode'],'--out',str(target)]
    try:
        runpy.run_path(sys.argv[0],run_name='__main__')
        row=json.loads((target/'metrics.json').read_text())
        rows.append({'id':case['id'],'status':row['status']})
        failed=row['status']!='PASS'
    except BaseException:
        traceback.print_exc();rows.append({'id':case['id'],'status':'FAIL'});failed=True
result={'status':'FAIL' if failed else 'PASS','formal_result':False,
        'scope':'development repeated training within one process/session','cases':rows}
(a.out/'suite.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'event':'RESULTS_READY',**result}),flush=True)
# Keep the channel alive while the runner saves tensors and live bindings.
deadline=time.monotonic()+a.hold_seconds
while time.monotonic()<deadline and not (a.out/'collected').exists():time.sleep(.2)
raise SystemExit(1 if failed else 0)
