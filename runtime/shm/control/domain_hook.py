#!/usr/bin/env python3
"""KubeVirt Sidecar v1alpha2 onDefineDomain executable. No GPU changes."""
import argparse
import json
import os
import re
import xml.etree.ElementTree as ET

NS='http://libvirt.org/schemas/domain/qemu/1.0'
ET.register_namespace('qemu',NS)
def mutate(vmi, domain):
    annotations=vmi['metadata'].get('annotations',{})
    aid=annotations.get('flyt.dev/shm-allocation','')
    if not re.fullmatch('[0-9a-f]{32}',aid): raise ValueError('allocation missing')
    root=ET.fromstring(domain)
    if vmi.get('status',{}).get('migrationState') or vmi['spec'].get('evictionStrategy')=='LiveMigrate':
        raise ValueError('active SHM migration unsupported')
    # Hook volumePath and sharedComputePath must refer to the SAME PVC.
    with open('/flyt-channel/'+aid+'/ready.json') as f: ready=json.load(f)
    if ready['allocationId']!=aid or ready['generation']!=annotations.get('flyt.dev/shm-generation'):
        raise ValueError('backing generation mismatch')
    size=ready['regionBytes']
    if type(size)!=int or not 2**20<=size<=2**32 or size&(size-1): raise ValueError('invalid BAR size')
    if os.stat('/flyt-channel/'+aid+'/channel').st_size!=size: raise ValueError('backing size mismatch')
    command=root.find('{'+NS+'}commandline')
    if command is None: command=ET.SubElement(root,'{'+NS+'}commandline')
    if any('flyt-shm' in a.get('value','') for a in command): raise ValueError('duplicate SHM device')
    values=['-object',json.dumps({'qom-type':'memory-backend-file','id':'flyt-shm-memory',
        'mem-path':'/var/run/flyt-channel/'+aid+'/channel','size':size,'share':True}),
        '-device','ivshmem-plain,id=flyt-shm-device,memdev=flyt-shm-memory']
    for value in values: ET.SubElement(command,'{'+NS+'}arg',{'value':value})
    return ET.tostring(root,encoding='unicode')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vmi',required=True);p.add_argument('--domain',required=True)
    a=p.parse_args();print(mutate(json.loads(a.vmi),a.domain))
