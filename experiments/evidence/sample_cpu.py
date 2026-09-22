#!/usr/bin/env python3
"""Sample disjoint cgroup-v2 CPU counters; never sum a parent and its child."""
import argparse
import json
from pathlib import Path
import time


def validate(paths):
    paths = [Path(p).resolve(strict=True) for p in paths]
    root = Path("/sys/fs/cgroup")
    for p in paths:
        if root not in p.parents: raise ValueError("Expected an explicit cgroup below /sys/fs/cgroup")
    for i, p in enumerate(paths):
        for q in paths[i + 1:]:
            if p == q or p in q.parents or q in p.parents: raise ValueError("Overlapping cgroups would double-count CPU")
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cgroup", action="append", required=True)
    p.add_argument("--seconds", type=float, required=True); p.add_argument("--interval", type=float, default=1)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.seconds <= 0 or a.interval <= 0: p.error("positive duration/interval required")
    paths = validate(a.cgroup)
    identities = {str(p): p.stat().st_ino for p in paths}
    deadline = time.monotonic() + a.seconds
    with a.out.open("x") as f:
        while True:
            values = {}
            for path in paths:
                try:
                    if path.stat().st_ino != identities[str(path)]: raise RuntimeError("cgroup replaced")
                    values[str(path)] = {k: int(v) for k, v in (line.split() for line in (path / "cpu.stat").read_text().splitlines())}
                except (OSError, RuntimeError) as e:
                    values[str(path)] = {"error": str(e)}
            now = time.monotonic()
            f.write(json.dumps({"host_monotonic": now, "wall_ns": time.time_ns(), "cgroups": values}) + "\n"); f.flush()
            if now >= deadline: break
            time.sleep(min(a.interval, deadline - now))


if __name__ == "__main__": main()
