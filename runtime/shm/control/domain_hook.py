#!/usr/bin/env python3
"""KubeVirt Sidecar v1alpha2 onDefineDomain executable. No GPU changes."""
import argparse
import json
import os
import re
import xml.etree.ElementTree as ET

NS='http://libvirt.org/schemas/domain/qemu/1.0'
ET.register_namespace('qemu',NS)
def pci_location(root):
    """Reserve an explicit free root-bus slot before QEMU parses custom argv.

    Automatic placement of custom argv happens before libvirt's devices and can
    steal VGA's slot. Exclude chipset slots 0/31 and all root-bus XML addresses.
    """
    devices=root.find('devices')
    if devices is None:raise ValueError('domain devices missing')
    controllers=[x for x in devices.findall('controller') if x.get('type')=='pci' and x.get('index','0')=='0']
    if controllers:
        if len(controllers)!=1 or controllers[0].get('model') not in ('pcie-root','pci-root'):
            raise ValueError('supported PCI root controller required')
        bus='pcie.0' if controllers[0].get('model')=='pcie-root' else 'pci.0'
    else:
        # KubeVirt also calls the hook before libvirt supplies implicit roots.
        machine=root.find('./os/type')
        name=machine.get('machine','') if machine is not None else ''
        if name=='q35' or name.startswith('pc-q35-'):bus='pcie.0'
        else:raise ValueError('explicit PCI root or known q35 machine required')
    used={0,31}
    for address in devices.iter('address'):
        if address.get('type')=='pci' and int(address.get('bus','0'),0)==0:
            used.add(int(address.get('slot','0'),0))
    for slot in range(30,0,-1):
        if slot not in used:return (bus,slot)
    raise ValueError('no free PCI root slot for SHM')
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
    bus,slot=pci_location(root)
    values=['-object',json.dumps({'qom-type':'memory-backend-file','id':'flyt-shm-memory',
        'mem-path':'/var/run/flyt-channel/'+aid+'/channel','size':size,'share':True}),
        '-device',f'ivshmem-plain,id=flyt-shm-device,memdev=flyt-shm-memory,bus={bus},addr=0x{slot:x}']
    for value in values: ET.SubElement(command,'{'+NS+'}arg',{'value':value})
    return ET.tostring(root,encoding='unicode')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vmi',required=True);p.add_argument('--domain',required=True)
    a=p.parse_args();print(mutate(json.loads(a.vmi),a.domain))
