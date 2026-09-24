#!/usr/bin/env python3
"""Run sharing, memory and normal release evidence without fault injection.

Keep failed attempts. Never force finalizers or reuse a non-Released allocation.
Run the read-only dashboard/capture separately before starting this coordinator.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = 'flyt-evidence'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--images', type=Path, required=True)
    p.add_argument('--artifacts', type=Path, required=True)
    p.add_argument('--prefix', default='evidence-show')
    p.add_argument('--pvc-a', default='evidence-vm-a-backing')
    p.add_argument('--pvc-b', default='evidence-pair-b-backing')
    p.add_argument('--lifecycle-count', type=int, default=20)
    p.add_argument('--include-compute', action='store_true', help='Exploratory 25/50/100 load characterization, three repeats; not a limiter PASS verdict')
    a = p.parse_args()
    if not re.fullmatch(r'evidence-[a-z0-9-]{1,16}', a.prefix):
        p.error('short evidence- prefix required')
    if not 1 <= a.lifecycle_count <= 20 or a.pvc_a == a.pvc_b:
        p.error('count 1..20 and distinct PVCs required')
    base = a.output.resolve()
    images = json.loads(a.images.read_text())
    art = a.artifacts.resolve()
    base.mkdir(parents=True, exist_ok=False)
    runs = base / 'runs'
    runs.mkdir()
    summary, owned, jobs = [], {}, []

    def call(argv):
        return subprocess.check_output([str(x) for x in argv], cwd=ROOT, text=True, timeout=240)

    def get_channel(name):
        data = call(['kubectl', 'get', 'flytsharedmemorychannel', name+'-channel', '-n', NS, '--ignore-not-found', '-o', 'json'])
        return json.loads(data) if data.strip() else None

    def prepare(name, pvc, memory=4096, sessions=1, compute=50):
        dest = runs / name
        print(call([sys.executable, ROOT/'scripts/prepare-evidence-vm.py', '--name', name,
                    '--reuse-pvc', pvc, '--public-key', art/'guest-key.pub',
                    '--control-image', images['control'], '--worker-image', images['worker'],
                    '--hook-image', images['hook'], '--memory-mib', memory, '--compute', compute,
                    '--sessions', sessions, '--output', dest/'prepare']), flush=True)
        owned[name] = get_channel(name)['metadata']['uid']
        call(['kubectl', 'wait', '-n', NS, 'flytsharedmemorychannel/'+name+'-channel',
              '--for=jsonpath={.status.phase}=BackingReady', '--timeout=180s'])
        return dest

    def launch(name, dest, extra):
        log = (dest/'runner.log').open('w')
        proc = subprocess.Popen([sys.executable, str(ROOT/'scripts/run-evidence-smoke.py'),
                '--name', name, '--key', str(art/'guest-key'), '--artifacts', str(art),
                '--output', str(dest/'run'), *map(str, extra)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        job = (name, dest, proc, log)
        jobs.append(job)
        return job

    def finish(job):
        name, dest, proc, log = job
        code = proc.wait()
        log.close()
        f = dest/'run/metrics.json'
        metrics = json.loads(f.read_text()) if f.exists() else {'status': 'FAIL', 'error': 'missing metrics'}
        pods = json.loads(call(['kubectl', 'get', 'pods', '-n', NS, '-o', 'json']))['items']
        residual = [x['metadata']['name'] for x in pods if x['metadata']['name'] == name+'-channel-worker'
                    or x['metadata']['name'].startswith('virt-launcher-'+name+'-')]
        row = {'name': name, 'exit_code': code, 'metrics': metrics, 'residual_execution_pods': residual}
        summary.append(row)
        (base/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps(row), flush=True)
        if code or residual or metrics['status'] != 'PASS':
            raise RuntimeError('Failed run; inspect evidence before retry: '+name)

    try:
        pvcs = [a.pvc_a, a.pvc_b]
        barrier = base/'pair-barrier'
        pair = []
        for i in range(2):
            name = a.prefix+'-pair-'+('a' if i == 0 else 'b')
            dest = prepare(name, pvcs[i])
            pair.append(launch(name, dest, ['--seed', 2026+i, '--gpu-seconds', 45, '--barrier', barrier]))
        deadline = time.monotonic()+300
        while len(list(barrier.glob('*.ready'))) != 2:
            if time.monotonic() > deadline or any(j[2].poll() is not None for j in pair):
                raise RuntimeError('Pair could not reach barrier')
            time.sleep(1)
        (barrier/'start').touch()
        for job in pair:
            finish(job)
        for quota in [1024, 4096]:
            for scenario in ['observe', 'suite']:
                name = f'{a.prefix}-mem-{quota}-{scenario}'
                dest = prepare(name, pvcs[0], quota, compute=100)
                finish(launch(name, dest, ['--probe', 'memory', '--scenario', scenario, '--bytes', quota*1024*1024]))
        name = a.prefix+'-aggregate'
        dest = prepare(name, pvcs[0], sessions=2, compute=100)
        finish(launch(name, dest, ['--probe', 'memory', '--scenario', 'aggregate_race', '--bytes', 4294967296]))
        for offset in range(0, a.lifecycle_count, 2):
            round_jobs = []
            for i in range(offset, min(offset+2, a.lifecycle_count)):
                name = f'{a.prefix}-life-{i+1:02}'
                round_jobs.append(launch(name, prepare(name, pvcs[i % 2]), ['--seed', 3000+i]))
            for job in round_jobs:
                finish(job)
        if a.include_compute:
            for repetition in range(1, 4):
                for compute in ([100, 50, 25] if repetition % 2 else [25, 50, 100]):
                    name = f'{a.prefix}-compute-{compute}-{repetition}'
                    dest = prepare(name, pvcs[0], compute=compute)
                    finish(launch(name, dest, ['--gpu-program', 'compute', '--gpu-seconds', 71]))
    finally:
        # Normal drain only, limited to UIDs this invocation created. Preserve
        # finalizers when release cannot be proved; never force-delete.
        cleanup = []
        for name, uid in owned.items():
            try:
                c = get_channel(name)
                if c and c['metadata']['uid'] == uid and c.get('status', {}).get('phase') != 'Released':
                    call(['kubectl', 'patch', 'flytsharedmemorychannel', name+'-channel', '-n', NS,
                          '--type=json', '-p', json.dumps([{'op':'test','path':'/metadata/uid','value':uid},
                                                        {'op':'add','path':'/spec/drain','value':True}])])
                    call(['kubectl','wait','-n',NS,'flytsharedmemorychannel/'+name+'-channel',
                          '--for=jsonpath={.status.phase}=Released','--timeout=180s'])
                cleanup.append({'name':name,'phase':(get_channel(name) or {}).get('status',{}).get('phase')})
            except Exception as error:
                cleanup.append({'name':name,'error':str(error)})
        (base/'cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
        for _, _, proc, log in jobs:
            if proc.poll() is None:
                try:proc.wait(timeout=30)
                except subprocess.TimeoutExpired:proc.terminate()
            log.close()


if __name__ == '__main__':
    main()
