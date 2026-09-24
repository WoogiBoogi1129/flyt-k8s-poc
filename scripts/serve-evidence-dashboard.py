#!/usr/bin/env python3
"""Read-only live SHM/HAMi dashboard. Store the exact snapshots shown in a browser.

No arbitrary commands or Kubernetes mutations are exposed over HTTP. Bind locally.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = '''<!doctype html><meta charset="utf-8"><title>SHM / HAMi live evidence</title>
<style>body{font:16px system-ui;background:#0c1525;color:#e6eef9;margin:30px}h1{font-size:28px}h2{font-size:18px;color:#7dd3fc}small{color:#a7b6c9}section{background:#142238;border:1px solid #30445e;padding:18px;margin:14px 0;border-radius:8px}pre{font:13px monospace;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5;margin:8px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.grid section{margin:0}#stamp{color:#a7f3d0}</style>
<h1>SHM / HAMi — live command evidence</h1><div id="stamp">Waiting for collector</div>
<small>Actual Kubernetes / NVIDIA / HAMi observations. Configuration is not measured enforcement. Development run.</small>
<section><h2>VM → Channel → Worker allocation</h2><pre id="route"></pre></section>
<div class="grid"><section><h2>Physical GPU and processes</h2><pre id="gpu"></pre></section><section><h2>Guest command and allocation stages</h2><pre id="guest"></pre></section></div>
<section><h2>HAMi monitor — raw measured values (bytes / utilization)</h2><pre id="hami"></pre></section>
<section><h2>Latest completed runs — normal release</h2><pre id="results"></pre></section>
<script>async function update(){try{let r=await fetch('/snapshot');if(!r.ok)throw Error('collector unavailable');let d=await r.json();for(let k of ['route','gpu','guest','hami','results'])document.getElementById(k).textContent=d[k];document.getElementById('stamp').textContent=d.timestamp+' | snapshot '+d.sequence+' | '+d.run_id;window.evidenceSnapshot=d;document.body.dataset.sequence=d.sequence}catch(e){document.getElementById('stamp').textContent='COLLECTION ERROR: '+e}}update();setInterval(update,1500)</script>'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prefix',default='evidence-show-')
    p.add_argument('--gpu-uuid',default='GPU-7d708c42-8d4a-16d5-0746-474567157aa3')
    p.add_argument('--monitor-url',default='http://192.168.24.21:31992/metrics')
    p.add_argument('--port',type=int,default=8765)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    state={};lock=threading.Lock()
    def command(argv):
        r=subprocess.run(argv,text=True,capture_output=True,timeout=15)
        if r.returncode:raise RuntimeError(r.stderr.strip())
        return r.stdout
    def k(kind,ns='flyt-evidence'):
        return json.loads(command(['kubectl','get',kind,'-n',ns,'-o','json']))['items']
    def collect():
        seq=0
        while True:
            began=dt.datetime.now(dt.timezone.utc).isoformat();seq+=1
            d={'sequence':seq,'timestamp':began,'run_id':a.runs.name,'errors':[]}
            try:
                channels=[x for x in k('flytsharedmemorychannels') if x['metadata']['name'].startswith(a.prefix)]
                pods=[x for x in k('pods') if x['metadata']['name'].startswith(a.prefix)]
                vmis=[x for x in k('vmi') if x['metadata']['name'].startswith(a.prefix)]
                routes=[]
                for c in channels:
                    s=c.get('status',{});spec=c['spec'];pod=next((p for p in pods if p['metadata']['uid']==s.get('workerPodUID')),None)
                    routes.append({'channel':c['metadata']['name'],'channel_uid':c['metadata']['uid'],'vm':spec['vmRef']['name'],'phase':s.get('phase'),'allocation':s.get('allocation'),'generation':s.get('generation'),'gpu_uuid':s.get('gpuUUID'),'memory_mib':s.get('memoryMiB'),'compute':s.get('compute'),'worker_uid':s.get('workerPodUID'),'worker':pod['metadata']['name'] if pod else None,'hami_allocation':pod['metadata'].get('annotations',{}).get('hami.io/vgpu-devices-allocated') if pod else None,'worker_image_ids':[i.get('imageID') for i in pod.get('status',{}).get('containerStatuses',[])] if pod else [],'vmi_uid':s.get('vmiUID'),'vmi_phase':next((v.get('status',{}).get('phase') for v in vmis if v['metadata']['uid']==s.get('vmiUID')),None)})
                d['routes']=routes
                lines=['$ kubectl get vmi,flytsharedmemorychannels,pods -n flyt-evidence (selected fields)', '$ kubectl get pod WORKER -n flyt-evidence -o json (HAMi allocation / imageID)']
                visible=[r for r in routes if r['phase']!='Released']+[r for r in routes if r['phase']=='Released'][-2:]
                for r in visible[:4]:
                    lines.extend([f"{r['vm']} | VMI={r['vmi_phase']} | Channel={r['phase']} | memory={r['memory_mib']} MiB compute={r['compute']}",f"  GPU={r['gpu_uuid']} | Worker={r['worker']}",f"  HAMi={r['hami_allocation']}",f"  allocation={r['allocation']} | Worker UID={r['worker_uid']}"])
                d['route']='\n'.join(lines)
            except Exception as e:d['route']='ERROR: '+str(e);d['errors'].append(d['route']);d['routes']=[]
            try:
                base=['nvidia-smi','-i',a.gpu_uuid]  # Run collector on the GPU host to see host PIDs.
                out=[]
                for q in ['--query-gpu=timestamp,uuid,memory.used,memory.total,utilization.gpu','--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory']:
                    argv=base+[q,'--format=csv'];out.append('$ '+' '.join(argv)+'\n'+command(argv))
                d['gpu']='\n'.join(out)
            except Exception as e:d['gpu']='ERROR: '+str(e);d['errors'].append(d['gpu'])
            try:
                with urllib.request.urlopen(a.monitor_url,timeout=5) as r:raw=r.read().decode()
                d['hami']='$ curl '+a.monitor_url+' (target GPU / experiment Worker lines)\n'+'\n'.join(x for x in raw.splitlines() if not x.startswith('#') and a.gpu_uuid in x and ('hami_host_gpu_memory_used_bytes' in x or (a.prefix in x and ('device_memory_bytes' in x or 'device_utilization_ratio' in x))))
            except Exception as e:d['hami']='ERROR: '+str(e);d['errors'].append(d['hami'])
            guest=[];results=[];stages=[]
            for path in sorted(a.runs.glob('*/run/guest-stdout.txt')):
                metrics=path.parent/'metrics.json'
                if metrics.exists():
                    m=json.loads(metrics.read_text());results.append(f"{path.parent.parent.name}: {m['status']} | released={m.get('released')} | {m.get('elapsed_seconds',0):.2f}s");continue
                cmd=path.parent/'guest-command.json'
                if cmd.exists():
                    c=json.loads(cmd.read_text());guest.append(f"{path.parent.parent.name} | BAR={c['bdf']} | slot={c['slot']}\n$ {c['binary']} {c['argv']}")
                output=path.read_text();guest.extend(output.splitlines()[-7:])
                for line in output.splitlines():
                    if line.startswith('{'):
                        try:
                            row=json.loads(line)
                            if 'stage' in row:stages.append({'run':path.parent.parent.name,'stage':row['stage']})
                        except ValueError:pass
            d['guest']='\n'.join(guest) or 'No active guest output';d['results']='\n'.join(results[-8:]) or 'No completed runs';d['stages']=stages
            d['completed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
            (a.output/f'{seq:06d}.json').write_text(json.dumps(d,indent=2)+'\n')
            with lock:state.clear();state.update(d)
            time.sleep(2)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/':data=HTML.encode();kind='text/html'
            elif self.path=='/snapshot':
                with lock:data=json.dumps(state).encode()
                kind='application/json'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    threading.Thread(target=collect,daemon=True).start()
    print(f'Live dashboard: http://127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
if __name__=='__main__':main()
