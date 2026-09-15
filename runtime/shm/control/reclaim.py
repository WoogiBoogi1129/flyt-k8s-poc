"""Run only after controller has persisted both detach acknowledgements."""
import argparse
import json
import os
import re
import stat

def reclaim(root,allocation,generation):
    if not re.fullmatch('[0-9a-f]{32}',allocation) or not re.fullmatch('[0-9a-f]{32}',generation):raise ValueError('invalid identity')
    base=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        directory=os.open(allocation,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=base)
        try:
            fd=os.open('ready.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
            with os.fdopen(fd) as f:ready=json.load(f)
            if ready['allocationId']!=allocation or ready['generation']!=generation:raise ValueError('stale reclamation')
            names=os.listdir(directory)
            if set(names)!={'channel','layout.bin','ready.json'}:raise ValueError('unexpected allocation contents')
            for name in names:
                st=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:raise ValueError('unsafe allocation entry')
            for name in ('channel','layout.bin','ready.json'):os.unlink(name,dir_fd=directory)
            os.fsync(directory)
        finally:os.close(directory)
        os.rmdir(allocation,dir_fd=base);os.fsync(base)
    finally:os.close(base)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--allocation',required=True);p.add_argument('--generation',required=True)
    a=p.parse_args();reclaim(a.root,a.allocation,a.generation)
