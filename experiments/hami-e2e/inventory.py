#!/usr/bin/env python3
"""Run inside the selected Worker; /proc and metadata reads only, no CUDA calls."""
import hashlib
import json
import os
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def collect():
    allowed = {"FLYT_RESOURCE_BACKEND", "FLYT_BINDING_API", "FLYT_GPU_UUID", "FLYT_MEMORY_BYTES",
               "FLYT_WORKER_GENERATION", "FLYT_POD_UID", "GPU_CORE_UTILIZATION_POLICY"}
    records = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            executable = os.readlink(proc / "exe")
            if Path(executable).name != "cricket-rpc-server":
                continue
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            env = dict(item.split(b"=", 1) for item in (proc / "environ").read_bytes().split(b"\0") if b"=" in item)
            selected = {k: env[k.encode()].decode() for k in allowed if k.encode() in env}
            libraries = sorted({line.split(maxsplit=5)[5].strip() for line in (proc / "maps").read_text().splitlines()
                                if len(line.split(maxsplit=5)) == 6 and ".so" in line.split(maxsplit=5)[5]})
            hami = [p for p in libraries if Path(p).name == "libvgpu.so"]
            records.append({"pid": int(proc.name), "start_ticks": stat[19], "ppid": int(stat[1]),
                            "process_group": int(stat[2]), "executable": executable,
                            "executable_sha256": digest(proc / "exe"), "environment": selected,
                            "mps_setting_present": any(k.startswith(b"CUDA_MPS_") for k in env),
                            "libraries": libraries, "hami_sha256": {p: digest(p) for p in hami}})
        except FileNotFoundError:
            continue  # Process disappeared; no reusable identity is emitted.
    meta = {}
    for name in ("FLYT_COMMIT", "PATCHES.sha256", "STAGE2_PATCHES.sha256", "STAGE3_PATCHES.sha256", "STAGE5_PATCHES.sha256", "STAGE5.json", "STAGE6.json"):
        path = Path("/opt/flyt/metadata") / name
        if path.exists():
            meta[name] = path.read_text()[:32768]
    return {"schema": 1, "pod_uid": os.environ.get("FLYT_POD_UID"),
            "node_manager_pid": int(Path("/run/flyt/node-manager.pid").read_text()),
            "rpc": records, "metadata": meta}


if __name__ == "__main__":
    print(json.dumps(collect(), sort_keys=True))
