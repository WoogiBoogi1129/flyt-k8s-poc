#!/usr/bin/env python3
"""Read-only host NVIDIA observations for one explicitly selected GPU UUID.

Whole-device memory/utilization is not a per-VM quota verdict. Host cgroup paths
are included to help associate compute PIDs with saved Worker Pod identities.
"""
import argparse
import csv
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--gpu-uuid',required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--stop-file',type=Path,required=True);p.add_argument('--duration',type=int,default=7200)
a=p.parse_args()
if not re.fullmatch(r'GPU-[0-9a-fA-F-]{36}',a.gpu_uuid):p.error('explicit GPU UUID required')
if not 1<=a.duration<=14400:p.error('duration must be 1..14400 seconds')
def query(kind,fields):
    result=subprocess.run(['nvidia-smi','--query-'+kind+'='+','.join(fields),'--format=csv,noheader,nounits'],text=True,capture_output=True,timeout=5)
    if result.returncode:raise RuntimeError(result.stderr.strip())
    return [{key:value.strip() for key,value in zip(fields,row)} for row in csv.reader(result.stdout.splitlines()) if row]
deadline=time.monotonic()+a.duration
with a.output.open('x') as output:
    while time.monotonic()<deadline and not a.stop_file.exists():
        started=time.monotonic();record={'utc':datetime.now(timezone.utc).isoformat(),'monotonic_seconds':started}
        try:
            record['gpu']=[x for x in query('gpu',['uuid','memory.used','memory.total','utilization.gpu','temperature.gpu']) if x['uuid']==a.gpu_uuid]
            record['processes']=[x for x in query('compute-apps',['gpu_uuid','pid','process_name','used_gpu_memory']) if x['gpu_uuid']==a.gpu_uuid]
            for process in record['processes']:
                if process['pid'].isdigit():
                    try:process['host_cgroup']=(Path('/proc')/process['pid']/'cgroup').read_text().strip()
                    except (OSError,PermissionError) as error:process['cgroup_error']=type(error).__name__
        except Exception as error:record['error']=str(error)
        output.write(json.dumps(record)+'\n');output.flush()
        time.sleep(max(.01,1-(time.monotonic()-started)))
