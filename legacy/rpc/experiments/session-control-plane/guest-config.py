#!/usr/bin/env python3
"""Generate local stage-7 Guest config using the preserved stage-3 identity reader.

Read-only Kubernetes queries; no install, SSH, VM restart or CUDA execution.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--context", required=True)
    p.add_argument("--namespace", default="flyt-hami-stage3")
    p.add_argument("--worker", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    baseline = Path(__file__).resolve().parent.parent / "controller/guest-config.py"
    subprocess.run([sys.executable, str(baseline), "--context", a.context, "--namespace", a.namespace,
                    "--worker", a.worker, "--output", str(a.output)], check=True)
    snapshot = json.loads((a.output / "binding.json").read_text())
    worker, vmi = snapshot["workerUID"], snapshot["references"]["vmiRef"]["uid"]
    if not all(re.fullmatch(r"[0-9a-f-]{36}", v) for v in (worker, vmi)):
        raise ValueError("invalid snapshot UID")
    path = a.output / "client-mgr.toml"
    with path.open("a") as stream:
        stream.write(f'\n[session-control]\nprotocol = "flyt-session-v7"\nvmi-uid = "{vmi}"\nworker-uid = "{worker}"\n')
    snapshot["sessionProtocol"] = "flyt-session-v7"
    snapshot["note"] = "Stage-7 Guest and Manager must be installed together; no installation or CUDA validation performed"
    (a.output / "binding.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    print("Stage-7 local config written; existing CUDA sessions are not migrated")


if __name__ == "__main__":
    main()
