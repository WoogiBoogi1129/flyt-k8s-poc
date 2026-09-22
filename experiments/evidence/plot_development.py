#!/usr/bin/env python3
"""Plot observed development lifecycle timings; no training/performance claims."""
import argparse
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--lifecycle',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();rows=[]
for entry in json.loads((a.lifecycle/'summary.json').read_text()):
    events={v['state']:v['elapsed_seconds'] for v in json.loads((a.lifecycle/entry['name']/'run/timeline.json').read_text())}
    if entry['metrics']['status']!='PASS':continue
    rows.append({'name':entry['name'],'ready_seconds':events['ChannelReady']-events['VMStartRequested'],
                 'release_seconds':events['Released']-events['DrainRequested']})
if not rows:raise SystemExit('No successful complete lifecycle observations')
a.output.mkdir(parents=True,exist_ok=True)
fig,axes=plt.subplots(1,2,figsize=(9,3.4),layout='constrained')
for ax,key,title in zip(axes,['ready_seconds','release_seconds'],['VM start to Channel Ready','Drain request to Released']):
    ax.plot(range(1,len(rows)+1),[r[key] for r in rows],marker='o',markersize=3,color='#2563a6')
    ax.set(xlabel='Independent VM allocation',ylabel='Seconds',title=title,ylim=(0,None));ax.grid(alpha=.2)
fig.suptitle('SHM development lifecycle: GPU smoke, not PyTorch training',fontsize=11)
fig.savefig(a.output/'lifecycle.png',dpi=180);plt.close(fig)
summary={'completed':len(rows),'rows':rows}
for key in ['ready_seconds','release_seconds']:
    values=[r[key] for r in rows];summary[key]={'median':statistics.median(values),'min':min(values),'max':max(values)}
(a.output/'lifecycle-summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
