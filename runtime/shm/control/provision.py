"""Offline per-allocation PVC initializer. Never reopen/overwrite an allocation."""
import argparse
import json
import mmap
import os
from pathlib import Path
import re
import struct

def identifier(value):
    if not re.fullmatch(r'[0-9a-f]{32}', value) or int(value,16)==0:
        raise ValueError('nonzero 128-bit identifier required')
    return bytes.fromhex(value)

def provision(root, allocation, generation, sessions, uid, gid):
    aid, gen = identifier(allocation), identifier(generation)
    ids = [identifier(s) for s in sessions]
    if not 1 <= len(ids) <= 32 or len(set(ids)) != len(ids):
        raise ValueError('1..32 unique session IDs required')
    # 16 MiB CUDA copy plus envelope; PCI BAR backing rounded to power of two.
    arena = 17 * 1024 * 1024
    pos, slots = 4096, []
    for sid in ids:
        req = pos; pos += 128 + 64*128
        resp = pos; pos += 128 + 64*128
        inp = pos; pos += arena
        out = pos; pos += arena
        slots.append((sid,req,resp,inp,out))
    size = 1 << (pos-1).bit_length()
    if size > 2**32: raise ValueError('backing too large')
    directory = Path(root) / allocation
    directory.mkdir(mode=0o750)  # exclusive, no exist_ok; incomplete attempt is retained
    os.chown(directory,uid,gid)
    fd = os.open(directory/'channel',os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o660)
    try:
        os.fchown(fd,uid,gid)
        os.posix_fallocate(fd,0,size)
        with mmap.mmap(fd,size) as m:
            # New file's allocated extents are zero; write only the ABI header.
            m[:60]=struct.pack('<8sHHI16s16sQI',b'FLYTCHN1',1,0,4096,aid,gen,size,len(ids))
            m.flush()
        os.fsync(fd)
    finally: os.close(fd)
    layout=struct.pack('<16s16sQI20x',aid,gen,size,len(ids))
    for sid,req,resp,inp,out in slots:
        layout += struct.pack('<16sQI4xQI4xQQQQ',sid,req,64,resp,64,inp,arena,out,arena)
    for name,data in [('layout.bin',layout),('ready.json',json.dumps({'allocationId':allocation,'generation':generation,'regionBytes':size,'sessions':sessions}).encode())]:
        fd=os.open(directory/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
        try: os.fchown(fd,uid,gid); os.write(fd,data); os.fsync(fd)
        finally: os.close(fd)
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--allocation',required=True)
    p.add_argument('--generation',required=True);p.add_argument('--session',action='append',required=True)
    p.add_argument('--uid',type=int,required=True);p.add_argument('--gid',type=int,required=True)
    a=p.parse_args();provision(a.root,a.allocation,a.generation,a.session,a.uid,a.gid)
