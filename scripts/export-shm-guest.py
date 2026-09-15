#!/usr/bin/env python3
"""Author Guest config from an explicitly exported Channel JSON; no cluster writes."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/shm/control'))
from provision import make_layout

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--channel-json',type=Path,required=True)
    p.add_argument('--bdf',required=True);p.add_argument('--slot',type=int,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();c=json.loads(a.channel_json.read_text());s=c.get('status',{})
    if c.get('kind')!='FlytSharedMemoryChannel' or s.get('phase') not in ('Bound','Ready') or c['metadata'].get('deletionTimestamp') or c['spec'].get('drain'):
        raise ValueError('bound live channel required')
    if not s.get('vmiUID') or not s.get('workerPodUID') or not 0<=a.slot<len(s['sessions']):raise ValueError('incomplete binding/slot')
    if not re.fullmatch('[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\\.[0-7]',a.bdf):raise ValueError('explicit Guest BDF required')
    layout,_=make_layout(s['allocation'],s['generation'],s['sessions']);a.output.mkdir(mode=0o700)
    files={'layout.bin':layout,'binding.json':json.dumps({'channelUID':c['metadata']['uid'],'generation':s['generation'],
        'vmiUID':s['vmiUID'],'workerPodUID':s['workerPodUID'],'slot':a.slot},indent=2).encode(),
        'guest.env':('FLYT_LAYOUT=/etc/flyt/layout.bin\nFLYT_IVSHMEM_BDF='+a.bdf+'\nFLYT_SLOT='+str(a.slot)+'\nLD_PRELOAD=/opt/flyt/guest/libflyt_guest.so\n').encode()}
    for name,data in files.items():
        fd=os.open(a.output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data)
