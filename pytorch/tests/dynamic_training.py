#!/usr/bin/env python3
"""Long-running PyTorch GEMM workload used while Flyt changes SM quotas."""

import argparse
import json
import time
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--seconds", type=int, default=0)
    parser.add_argument("--width", type=int, default=1536)
    parser.add_argument("--repeats", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    torch.manual_seed(101)
    left = torch.randn(args.width, args.width, device="cuda")
    right = torch.randn(args.width, args.width, device="cuda")
    result = torch.empty_like(left)
    torch.mm(left, right, out=result)
    torch.cuda.synchronize()

    deadline = time.monotonic() + args.seconds if args.seconds else None
    output_file = args.output.open("w") if args.output else None
    for iteration in range(args.iterations):
        if deadline is not None and time.monotonic() >= deadline:
            break
        started = time.perf_counter()
        for _ in range(args.repeats):
            torch.mm(left, right, out=result)
        torch.cuda.synchronize()
        checksum = float(result[0, 0].cpu())
        finite = bool(torch.isfinite(result[0, 0]).cpu())
        record = {
            "iteration": iteration,
            "epoch_s": time.time(),
            # Keep the field name consumed by the existing analyzer while the
            # value is a deterministic GEMM checksum rather than a loss.
            "loss": checksum,
            "latency_s": time.perf_counter() - started,
            "allocated": torch.cuda.memory_allocated(),
            "reserved": torch.cuda.memory_reserved(),
            "finite": finite,
        }
        rendered = json.dumps(record, sort_keys=True)
        print(rendered, flush=True)
        if output_file:
            print(rendered, file=output_file, flush=True)
        if not record["finite"]:
            raise RuntimeError("non-finite loss")
    if output_file:
        output_file.close()


if __name__ == "__main__":
    main()
