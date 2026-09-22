#!/usr/bin/env python3
"""Two-process/VM start barrier with host clock bounds and explicit abort commands."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import time
from evidence import write_json


async def run(spec, out):
    if len(spec["clients"]) != 2:
        raise ValueError("Exactly two independent clients required")
    for client in spec["clients"]:
        for key in ("argv", "cancel_argv"):
            if not isinstance(client.get(key), list) or not client[key] or not all(isinstance(s, str) for s in client[key]):
                raise ValueError("Each client needs explicit argv AND a remote-aware cancel_argv")
    out.mkdir(parents=True, exist_ok=False)
    processes, logs, events = [], [], []
    async def wait_event(process, wanted, index):
        while True:
            line = await process.stdout.readline()
            if not line: raise RuntimeError(f"Client {index} exited before {wanted}")
            events.append({"client": index, "host_monotonic": time.monotonic(), "line": line.decode().strip()})
            try: obj = json.loads(line)
            except ValueError: continue
            if obj.get("event") == wanted: return time.monotonic()
    try:
        for i, client in enumerate(spec["clients"]):
            log = (out / f"client{i}.stderr.txt").open("wb"); logs.append(log)
            processes.append(await asyncio.create_subprocess_exec(*client["argv"], stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=log, start_new_session=True))
        await asyncio.wait_for(asyncio.gather(*(wait_event(p, "READY", i) for i, p in enumerate(processes))), spec.get("start_timeout", 300))
        sent = []
        for p in processes:
            sent.append(time.monotonic()); p.stdin.write(b"GO\n")
        await asyncio.gather(*(p.stdin.drain() for p in processes))
        received = await asyncio.wait_for(asyncio.gather(*(wait_event(p, "STARTED", i) for i, p in enumerate(processes))), 30)
        started_bounds = [[a, b] for a, b in zip(sent, received)]
        async def finish(p, i):
            remaining = await p.stdout.read()
            (out / f"client{i}.stdout.txt").write_bytes(remaining)
            code = await p.wait()
            if code != 0: raise RuntimeError(f"Client {i} exited with {code}")
            return time.monotonic()
        completed = await asyncio.wait_for(asyncio.gather(*(finish(p, i) for i, p in enumerate(processes))), spec["training_timeout"])
        result = {"status": "PASS", "formal_result": False, "scope": "barrier/process completion only",
                  "start_bounds": started_bounds, "completion_observed": completed,
                  "makespan_upper_bound_seconds": max(completed) - min(sent)}
    except Exception as e:
        result = {"status": "FAIL", "formal_result": False, "reason": f"{type(e).__name__}: {e}", "cancellation": []}
        for client in spec["clients"]:
            try:
                p = await asyncio.create_subprocess_exec(*client["cancel_argv"], stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                try: code = await asyncio.wait_for(p.wait(), 30)
                except asyncio.TimeoutError:
                    os.killpg(p.pid, signal.SIGKILL); await p.wait(); raise
                result["cancellation"].append({"exit_code": code})
            except Exception as cancel_error:
                result["cancellation"].append({"error": str(cancel_error)})
    finally:
        for p in processes:
            if p.returncode is None:
                try: os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: await asyncio.wait_for(p.wait(), 5)
                except asyncio.TimeoutError:
                    os.killpg(p.pid, signal.SIGKILL); await p.wait()
        for log in logs: log.close()
    write_json(out / "events.json", events)
    write_json(out / "metrics.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("spec", type=Path); p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    result = asyncio.run(run(json.loads(a.spec.read_text()), a.out))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__": main()
