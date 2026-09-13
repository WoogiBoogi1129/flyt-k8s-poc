#!/usr/bin/env python3
"""Supervise one Worker generation, including all Node Manager RPC children."""
import ctypes
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

STATE = Path("/run/flyt")


def clear_previous_queue():
    # emptyDir and the Pod IPC namespace may outlive a container restart. Clear
    # only Flyt's known ftok queue, never HAMi caches or unrelated IPC objects.
    path = b"/tmp/flyt-servernode-queue"
    if not os.path.exists(path):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.ftok.restype = ctypes.c_int
    libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.msgget.restype = ctypes.c_int
    libc.msgctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    libc.msgctl.restype = ctypes.c_int
    key = libc.ftok(path, 0x42)
    if key == -1:
        raise OSError(ctypes.get_errno(), "ftok failed for previous Flyt queue")
    queue = libc.msgget(key, 0)
    if queue == -1:
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise OSError(ctypes.get_errno(), "cannot inspect previous Flyt queue")
    if libc.msgctl(queue, 0, None) == -1:  # IPC_RMID on Linux
        raise OSError(ctypes.get_errno(), "cannot remove previous Flyt queue")


def rpc_ready():
    try:
        return subprocess.run(["rpcinfo", "-p", "127.0.0.1"], timeout=3,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ready():
    # No CUDA workload is executed by the Kubernetes readiness probe.
    try:
        os.kill(int((STATE / "node-manager.pid").read_text()), 0)
        return (STATE / "registered").exists() and rpc_ready()
    except (OSError, ValueError):
        return False


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "ready":
        return 0 if ready() else 1
    if os.environ.get("FLYT_RESOURCE_BACKEND") != "hami":
        raise RuntimeError("This image requires FLYT_RESOURCE_BACKEND=hami")
    if any(key.startswith("CUDA_MPS_") for key in os.environ):
        raise RuntimeError("Remove all CUDA_MPS_* settings from the HAMi Worker")
    if os.environ.get("GPU_CORE_UTILIZATION_POLICY") != "force":
        raise RuntimeError("HAMi compute enforcement must use force policy")
    if not 1 <= int(os.environ["FLYT_MAX_CLIENTS"]) <= 32:
        raise ValueError("FLYT_MAX_CLIENTS must be 1..32")
    if int(os.environ["FLYT_MEMORY_BYTES"]) <= 0:
        raise ValueError("FLYT_MEMORY_BYTES must be positive")
    STATE.mkdir(parents=True, exist_ok=True)
    for name in ("registered", "node-manager.pid"):
        (STATE / name).unlink(missing_ok=True)
    clear_previous_queue()
    guard = subprocess.run(["/opt/flyt/bin/flyt-hami-guard"], check=True,
                           text=True, stdout=subprocess.PIPE, timeout=60)
    print(guard.stdout, end="", flush=True)
    values = [line.split("=", 1)[1] for line in guard.stdout.splitlines()
              if line.startswith("FLYT_GUARD_SM=")]
    if len(values) != 1 or int(values[0]) <= 0:
        raise RuntimeError("No unambiguous CUDA capability inventory from guard")
    os.environ["FLYT_REPORTED_SM"] = values[0]  # Capability, never quota percent.
    children = []

    def stop(_signum, _frame):
        raise InterruptedError("Worker termination requested")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        rpcbind = subprocess.Popen(["rpcbind", "-f"], start_new_session=True)
        children.append(rpcbind)
        deadline = time.monotonic() + 15
        while not rpc_ready():
            if rpcbind.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("rpcbind startup failed")
            time.sleep(0.2)
        node = subprocess.Popen(["/opt/flyt/bin/flyt-node-manager"], start_new_session=True)
        children.append(node)
        (STATE / "node-manager.pid").write_text(str(node.pid))
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("Worker process exited; ending all RPC sessions")
    except InterruptedError:
        return 0
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        (STATE / "registered").unlink(missing_ok=True)
        (STATE / "node-manager.pid").unlink(missing_ok=True)
        # Node Manager and its RPC children share a process group. Even when the
        # manager has already exited, kill its remaining RPC children.
        for child in reversed(children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(2)
        for child in reversed(children):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Worker failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
