#!/usr/bin/env python3
"""Stage-3 identity wrapper around the preserved stage-2 supervisor."""
import os
import runpy
import secrets
import sys

if len(sys.argv) == 1:
    if os.environ.get("FLYT_BINDING_API") != "1" or not os.environ.get("FLYT_POD_UID"):
        raise SystemExit("controlled Worker requires binding mode and a Kubernetes Pod UID")
    os.environ["FLYT_WORKER_GENERATION"] = secrets.token_hex(16)
runpy.run_path("/opt/flyt/worker-base.py", run_name="__main__")
