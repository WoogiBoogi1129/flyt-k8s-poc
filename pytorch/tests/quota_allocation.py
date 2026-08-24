#!/usr/bin/env python3
"""Allocate a requested tensor size and report a normal PyTorch OOM."""

import argparse
import json
import time

import torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mib", type=int, required=True)
    parser.add_argument("--expect", choices=("success", "oom"), required=True)
    parser.add_argument("--delay-seconds", type=int, default=0)
    args = parser.parse_args()
    torch.cuda.init()
    print(json.dumps({"state": "cuda-initialized", "delay_seconds": args.delay_seconds}), flush=True)
    if args.delay_seconds:
        time.sleep(args.delay_seconds)
    try:
        tensor = torch.empty(args.mib * 1024 * 1024, dtype=torch.uint8, device="cuda")
        tensor.fill_(7)
        torch.cuda.synchronize()
        result = "success"
        checksum = int(tensor[:1024].sum().cpu())
    except torch.OutOfMemoryError as error:
        result = "oom"
        checksum = None
        print(json.dumps({"result": result, "error": str(error)}), flush=True)
        if args.expect != result:
            raise
    else:
        print(json.dumps({"result": result, "checksum": checksum}), flush=True)
    if result != args.expect:
        raise SystemExit(f"expected {args.expect}, observed {result}")


if __name__ == "__main__":
    main()
