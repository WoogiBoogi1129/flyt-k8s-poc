#!/usr/bin/env python3
"""Summarize actual CUPTI calls; a trace is not a correctness verdict."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path):
    calls, kernels = Counter(), Counter()
    layouts = {}
    entered, exited = Counter(), Counter()
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["domain"] == "kernel_layout":
                kernels[row["kernel"]] += 1
                layouts[row["kernel"]] = row["parameters"]
                continue
            key = (row["domain"], row["api"], row["correlation_id"])
            if row["site"] == "enter":
                entered[key] += 1
                calls[(row["domain"], row["api"])] += 1
                if row["kernel"]:
                    kernels[row["kernel"]] += 1
            elif row["site"] == "exit":
                exited[key] += 1
            else:
                raise ValueError("Unknown callback site")
    return {"scope": "development native CUDA trace; not VM correctness or performance",
            "calls": [{"domain": d, "api": a, "count": n}
                      for (d, a), n in sorted(calls.items())],
            "kernels": dict(sorted(kernels.items())),
            "kernel_parameters": layouts,
            "unmatched_entries": sum((entered - exited).values()),
            "unmatched_exits": sum((exited - entered).values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.output.open("x") as stream:
        json.dump(summarize(args.trace), stream, indent=2)
