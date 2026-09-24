#!/usr/bin/env python3
"""Plot actual live snapshots; never infer quota enforcement from configuration."""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import re
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def gpu_values(text):
    memory, utilization, processes = None, None, []
    for line in text.splitlines():
        fields = [x.strip() for x in line.split(',')]
        if len(fields) == 5 and re.match(r'\d{4}/', fields[0]):
            memory = float(fields[2].split()[0])
            utilization = float(fields[4].split()[0])
        if len(fields) == 4 and fields[0].startswith('GPU-') and fields[1].isdigit():
            processes.append({'pid': int(fields[1]), 'memory_mib': float(fields[3].split()[0])})
    return memory, utilization, processes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshots', type=Path, required=True)
    p.add_argument('--runs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    samples = []
    for f in sorted(a.snapshots.glob('*.json')):
        d = json.loads(f.read_text())
        memory, utilization, processes = gpu_values(d['gpu'])
        stages = {s['run']: s['stage'] for s in d['stages']}
        for r in d['routes']:
            if r['phase'] != 'Ready':
                continue
            matches = re.findall(r'hami_container_device_memory_bytes\{[^\n]*pod="'+re.escape(r['vm'])+r'-channel-worker"[^\n]*\} ([\d.eE+-]+)', d['hami'])
            util_matches = re.findall(r'hami_container_device_utilization_ratio\{[^\n]*pod="'+re.escape(r['vm'])+r'-channel-worker"[^\n]*\} ([\d.eE+-]+)', d['hami'])
            samples.append({'timestamp':d['timestamp'],'sequence':d['sequence'],'run':r['vm'],
                            'stage':stages.get(r['vm'],''),'gpu_memory_mib':memory,'gpu_utilization_percent':utilization,
                            'gpu_process_memory_mib':sum(x['memory_mib'] for x in processes) if processes else None,
                            'hami_container_memory_mib':float(matches[0])/1048576 if matches else None,
                            'hami_container_utilization_raw':float(util_matches[0]) if util_matches else None,
                            'measurement_started':'MEASUREMENT_START' in d['guest'] and '"measurement_seconds"' not in d['guest'],
                            'collection_error':bool(d['errors'])})
    if samples:
        with (a.output/'samples.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(samples[0]),lineterminator='\n');w.writeheader();w.writerows(samples)
    memory_rows=[]
    for quota in [1024,4096]:
        rows=[r for r in samples if f'mem-{quota}-observe' in r['run']]
        if not rows:continue
        origin=dt.datetime.fromisoformat(rows[0]['timestamp']).timestamp()
        t=[dt.datetime.fromisoformat(r['timestamp']).timestamp()-origin for r in rows]
        fig,ax=plt.subplots(figsize=(10,4),layout='constrained')
        for key,label in [('gpu_memory_mib','Physical GPU used'),('gpu_process_memory_mib','GPU process used'),('hami_container_memory_mib','HAMi container accounting')]:
            ax.plot(t,[r[key] for r in rows],marker='.',label=label)
        seen=set()
        for x,r in zip(t,rows):
            if r['stage'] and r['stage'] not in seen:
                seen.add(r['stage']);ax.axvline(x,color='gray',alpha=.35);ax.text(x+.3,.97,r['stage'],rotation=90,va='top',transform=ax.get_xaxis_transform(),fontsize=8,color='#475569')
        ax.set(xlabel='Seconds since first Ready snapshot (UTC host clock)',ylabel='MiB',title=f'Memory allocation / release: configured quota {quota} MiB')
        ax.grid(alpha=.2);ax.legend(fontsize=8);fig.savefig(a.output/f'memory-{quota}.png',dpi=180);plt.close(fig)
        for stage in ['baseline','allocated','freed','reallocated']:
            selected=[r for r in rows if r['stage']==stage]
            memory_rows.append({'quota_mib':quota,'stage':stage,'samples':len(selected),**{key:statistics.median([r[key] for r in selected if r[key] is not None]) if any(r[key] is not None for r in selected) else None for key in ['gpu_memory_mib','gpu_process_memory_mib','hami_container_memory_mib']}})
    (a.output/'memory-summary.json').write_text(json.dumps(memory_rows,indent=2)+'\n')
    life=[]
    for f in sorted(a.runs.glob('*-life-*/run/metrics.json')):
        m=json.loads(f.read_text());events={r['state']:r['elapsed_seconds'] for r in json.loads((f.parent/'timeline.json').read_text())}
        if m['status']!='PASS':continue
        life.append({'run':f.parent.parent.name,'ready_seconds':events['ChannelReady']-events['VMStartRequested'],'release_seconds':events['Released']-events['DrainRequested']})
    if life:
        fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
        stats={}
        for ax,key,title in zip(axes,['ready_seconds','release_seconds'],['Start to Ready','Drain to Released']):
            vals=[r[key] for r in life];ax.plot(range(1,len(life)+1),vals,'o-');ax.set(title=title,xlabel='Fresh allocation',ylabel='Seconds',ylim=(0,None));ax.grid(alpha=.2)
            stats[key]={'median':statistics.median(vals),'min':min(vals),'max':max(vals)}
        fig.suptitle('Normal lifecycle — development observation, no fault injection')
        fig.savefig(a.output/'lifecycle.png',dpi=180);plt.close(fig)
        (a.output/'lifecycle-summary.json').write_text(json.dumps({'completed':len(life),'rows':life,**stats},indent=2)+'\n')
    compute=[]
    for f in sorted(a.runs.glob('*-compute-*/run/metrics.json')):
        name=f.parent.parent.name;m=json.loads(f.read_text())
        rows=[r for r in samples if r['run']==name and r['measurement_started'] and not r['collection_error'] and r['gpu_utilization_percent'] is not None]
        if not rows:continue
        values=[r['gpu_utilization_percent'] for r in rows]
        duration=(dt.datetime.fromisoformat(rows[-1]['timestamp'])-dt.datetime.fromisoformat(rows[0]['timestamp'])).total_seconds()
        hami_values=[r['hami_container_utilization_raw'] for r in rows if r['hami_container_utilization_raw'] is not None]
        compute.append({'run':name,'configured_compute':int(name.split('-')[-2]),'repetition':int(name.split('-')[-1]),'execution_status':m['status'],'samples':len(rows),'observed_span_seconds':duration,'hami_utilization_mean_raw':statistics.mean(hami_values) if hami_values else None,'gpu_utilization_mean_percent':statistics.mean(values),'gpu_utilization_min_percent':min(values),'gpu_utilization_max_percent':max(values),'formal_enforcement_verdict':'NOT_EVALUATED','scope':'Physical GPU utilization under isolated PTX load; not VM training performance'})
    if compute:
        fig,ax=plt.subplots(figsize=(9,4),layout='constrained')
        for r in compute:ax.scatter(r['configured_compute'],r['gpu_utilization_mean_percent'],label=r['run'].split('compute-')[1])
        ax.plot([25,50,100],[25,50,100],'--',color='gray',label='Configured value (reference only)');ax.set(xlabel='Configured compute',ylabel='Mean physical GPU utilization (%)',ylim=(0,105),title='Exploratory compute characterization — not enforcement PASS')
        ax.legend(fontsize=7);ax.grid(alpha=.2);fig.savefig(a.output/'compute-observed.png',dpi=180);plt.close(fig)
    (a.output/'compute-summary.json').write_text(json.dumps(compute,indent=2)+'\n')


if __name__ == '__main__':main()
