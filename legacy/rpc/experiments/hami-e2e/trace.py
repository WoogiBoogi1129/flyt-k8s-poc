#!/usr/bin/env python3
"""Explicit, bounded GDB trace of a pinned RPC PID; never changes ptrace policy.

Invoked only for a dedicated smoke run, while its guest is waiting at GO.
Missing gdb/binutils/permissions/symbols => BLOCKED, never interception PASS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


GDB_SCRIPT = r'''
import gdb, json, threading
from pathlib import Path
cfg = json.loads(CONFIG_JSON)
def emit(event, **fields):
    print("FLYT_TRACE " + json.dumps(dict(schema=1, run_id=cfg["run_id"], pid=cfg["pid"],
        start_ticks=cfg["start_ticks"], hami_sha256=cfg["sha256"], event=event, **fields)), flush=True)
hit = set()
finished = False
timer = None
class Entry(gdb.Breakpoint):
    def __init__(self, address, api, category):
        super().__init__("*0x%x" % address, internal=True)
        self.silent = True
        self.api, self.category, self.address = api, category, address
    def stop(self):
        # Breakpoint is at the ELF symbol address inside this process's libvgpu.
        hit.add(self.category)
        emit("ENTRY", api=self.api, category=self.category, address=hex(self.address), library=cfg["library"])
        self.enabled = False  # One event per symbol; this is not a profiler.
        return {"allocation", "launch"}.issubset(hit)
def interrupt():
    if not finished:
        try: gdb.execute("interrupt")
        except gdb.error: pass
try:
    gdb.execute("set pagination off")
    gdb.execute("set confirm off")
    gdb.execute("attach %d" % cfg["pid"])
    if Path("/proc/%d/stat" % cfg["pid"]).read_text().rsplit(")",1)[1].split()[19] != cfg["start_ticks"]:
        raise RuntimeError("PID changed before attach")
    for item in cfg["symbols"]:
        actual = gdb.solib_name(item["address"])
        if actual != cfg["library"]: raise RuntimeError("symbol is not in the selected HAMi library")
        Entry(item["address"], item["name"], item["category"])
    timer = threading.Timer(20, lambda: gdb.post_event(interrupt))
    timer.daemon = True
    timer.start()
    emit("READY")
    gdb.execute("continue")
    emit("RESULT", status="PASS" if {"allocation","launch"}.issubset(hit) else "BLOCKED")
except Exception:
    emit("RESULT", status="BLOCKED")
finally:
    finished = True
    if timer: timer.cancel()
    try: gdb.execute("detach")
    except gdb.error: pass
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--start-ticks", required=True)
    p.add_argument("--run-id", required=True)
    a = p.parse_args()
    if a.pid <= 1 or not re.fullmatch(r"[0-9a-f]{32}", a.run_id):
        raise ValueError("invalid target")
    proc = Path("/proc") / str(a.pid)
    if Path(os.readlink(proc / "exe")).name != "cricket-rpc-server":
        raise ValueError("not an RPC process")
    if proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19] != a.start_ticks:
        raise ValueError("PID was reused")
    mappings = [line.split(maxsplit=5) for line in proc.joinpath("maps").read_text().splitlines()]
    selected = [v for v in mappings if len(v) == 6 and Path(v[5]).name == "libvgpu.so" and int(v[2], 16) == 0]
    if len(selected) != 1:
        raise ValueError("no unique HAMi load base")
    m = selected[0]
    library = m[5]
    # This collector supports the standard ET_DYN, zero-based HAMi ELF layout.
    # Reject other layouts rather than placing a breakpoint using a guessed bias.
    header = subprocess.check_output(["readelf", "-h", library], text=True, timeout=5)
    loads = subprocess.check_output(["readelf", "-W", "-l", library], text=True, timeout=5)
    if "DYN" not in header or not any(re.search(r"LOAD\s+0x0+\s+0x0+\s", line) for line in loads.splitlines()):
        raise ValueError("unsupported ELF load layout")
    base = int(m[0].split("-")[0], 16)
    symbols = []
    categories = {"cuMemAlloc": "allocation", "cuMemAlloc_v2": "allocation", "cuLaunchKernel": "launch", "cuLaunchKernel_ptsz": "launch"}
    nm = subprocess.check_output(["nm", "-D", "--defined-only", library], text=True, timeout=5)
    addresses = set()
    for line in nm.splitlines():
        v = line.split()
        if len(v) != 3 or v[1] not in ("T", "W"):
            continue
        name = v[2].split("@")[0]
        if name in categories:
            address = base + int(v[0], 16)
            if address not in addresses:
                symbols.append(dict(address=address, name=name, category=categories[name]))
                addresses.add(address)
    if {v["category"] for v in symbols} != {"allocation", "launch"}:
        raise ValueError("required HAMi entry symbols unavailable")
    with open(library, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(f.read()).hexdigest()
    cfg = dict(run_id=a.run_id, pid=a.pid, start_ticks=a.start_ticks, sha256=sha, library=library, symbols=symbols)
    with tempfile.TemporaryDirectory(prefix="flyt-e2e-trace-") as folder:
        path = Path(folder) / "trace.gdb"
        path.write_text("python\n" + GDB_SCRIPT.replace("CONFIG_JSON", repr(json.dumps(cfg))) + "\nend\n")
        # Outer timeout is a backstop for attach/loader hangs. GDB detaches on exit.
        return subprocess.call(["timeout", "--signal=INT", "--kill-after=5s", "30s", "gdb", "-nx", "-nh", "--batch", "-x", str(path)])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('FLYT_TRACE {"schema":1,"event":"UNAVAILABLE","status":"BLOCKED"}', flush=True)
        raise SystemExit(2)
