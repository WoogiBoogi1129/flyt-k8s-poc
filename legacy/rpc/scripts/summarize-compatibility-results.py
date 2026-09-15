#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path


GROUPS = [
    ("environment", "environment"),
    ("tensor-runtime", "tensor_runtime"),
    ("autograd-optimizer", "autograd_optimizer"),
    ("cublas", "cublas"),
    ("amp", "amp"),
    ("allocator", "allocator"),
    ("serialization", "serialization"),
    ("rng", "rng"),
    ("fft", "fft"),
    ("sparse", "sparse"),
    ("linalg", "linalg"),
    ("compile-modes", "compile_modes"),
    ("allocator-async", "allocator_cuda_malloc_async"),
    ("stream-event", "stream_event"),
    ("cuda-graph", "cuda_graph"),
    ("models", "models"),
    ("cudnn", "cudnn"),
    ("sdpa", "sdpa"),
    ("pinned-memory", "pinned_memory"),
]

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def classify(text: str, status: str) -> str:
    if status == "pass":
        return "pass"
    if "Segmentation fault" in text or "Fatal Python error" in text:
        return "segmentation-fault"
    if "cudaDeviceGetDefaultMemPool Not implemented" in text:
        return "mempool-not-implemented"
    if "CUFFT_INTERNAL_ERROR" in text:
        return "cufft-internal-error"
    if "Tried to allocate 32.00 GiB" in text:
        return "invalid-32gib-allocation"
    if "CUDA error: Not supported" in text:
        return "not-supported"
    if "checkBinaryArchMatches()" in text:
        return "binary-architecture-mismatch"
    return "error"


def result_for(vm: str, group: str, test: str, directories: list[Path]):
    matches = [d / f"{vm}-{group}.jsonl" for d in directories]
    paths = [path for path in matches if path.is_file()]
    if not paths:
        return "missing", "missing", ""
    path = paths[-1]
    text = ANSI.sub("", path.read_text(errors="replace"))
    status = "fail"
    error = ""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = item.get(test)
        if isinstance(value, dict) and value.get("status") in {"pass", "fail"}:
            status = value["status"]
            error = str(value.get("details", {}).get("error", ""))
            break
    return status, classify(text, status), error.replace("\t", " ").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vms", nargs="+", default=["flyt-vm-a", "flyt-vm-b"])
    args = parser.parse_args()

    rows = []
    for vm in args.vms:
        for group, test in GROUPS:
            status, category, error = result_for(vm, group, test, args.input)
            rows.append((vm, test, status, category, error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        stream.write("vm\ttest\tstatus\tcategory\terror\n")
        for row in rows:
            stream.write("\t".join(row) + "\n")

    for vm in args.vms:
        selected = [row for row in rows if row[0] == vm]
        passed = sum(row[2] == "pass" for row in selected)
        failed = sum(row[2] == "fail" for row in selected)
        missing = sum(row[2] == "missing" for row in selected)
        print(f"vm={vm} pass={passed} fail={failed} missing={missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
