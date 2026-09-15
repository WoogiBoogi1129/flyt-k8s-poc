#!/usr/bin/env python3
"""Evaluate the bounded Flyt SM-reallocation experiment."""

import argparse
import json
import math
import re
import statistics
from pathlib import Path


def phase_times(path):
    phases = {}
    pattern = re.compile(r"(?:^|\s)phase=([^\s]+).*?epoch_s=([0-9]+)")
    for line in path.read_text().splitlines():
        match = pattern.search(line)
        if match:
            phases[match.group(1)] = float(match.group(2))
    required = {"initial", "a92", "b92"}
    missing = sorted(required - phases.keys())
    if missing:
        raise ValueError(f"missing control phase timestamps: {missing}")
    return phases


def records(path, min_epoch, max_epoch):
    unique = {}
    for line in path.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not {"iteration", "epoch_s", "latency_s", "finite"} <= item.keys():
            continue
        if not min_epoch <= float(item["epoch_s"]) <= max_epoch:
            continue
        unique[(int(item["iteration"]), float(item["epoch_s"]))] = item
    ordered = sorted(unique.values(), key=lambda item: item["epoch_s"])
    if not ordered:
        raise ValueError(f"no training records in {path}")
    if not all(item["finite"] and math.isfinite(item["loss"]) for item in ordered):
        raise ValueError(f"non-finite training record in {path}")
    iterations = [int(item["iteration"]) for item in ordered]
    if iterations != list(range(iterations[0], iterations[-1] + 1)):
        raise ValueError(f"non-contiguous iterations in {path}")
    return ordered


def window(items, start, end):
    return [float(item["latency_s"]) for item in items if start <= item["epoch_s"] < end]


def evaluate_vm(items, baseline_start, baseline_end, boosted_start, boosted_end):
    baseline = window(items, baseline_start, baseline_end)
    boosted = window(items, boosted_start, boosted_end)
    if len(baseline) < 30 or len(boosted) < 30:
        raise ValueError(
            f"insufficient samples: baseline={len(baseline)}, boosted={len(boosted)}"
        )
    baseline_median = statistics.median(baseline)
    boosted_median = statistics.median(boosted)
    ratio = boosted_median / baseline_median
    return {
        "baseline_samples": len(baseline),
        "boosted_samples": len(boosted),
        "baseline_median_s": baseline_median,
        "boosted_median_s": boosted_median,
        "latency_ratio": ratio,
        "improved_by_at_least_5pct": ratio <= 0.95,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--vm-a", type=Path, required=True)
    parser.add_argument("--vm-b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    phases = phase_times(args.control)
    # systemd's persistent journal can contain records from earlier validation
    # attempts whose iteration counters restart at zero. Restrict continuity
    # checks and performance windows to this control run.
    run_start = phases["initial"] - 60
    run_end = phases["b92"] + 180
    vm_a = records(args.vm_a, run_start, run_end)
    vm_b = records(args.vm_b, run_start, run_end)
    # Training starts 45 seconds before the first change. Discard transitions
    # around each control transition so RPC/server reconfiguration is excluded.
    result = {
        "vm_a": evaluate_vm(
            vm_a,
            phases["initial"] - 40,
            phases["initial"] - 5,
            phases["initial"] + 2,
            phases["a92"] - 2,
        ),
        "vm_b": evaluate_vm(
            vm_b,
            phases["initial"] - 40,
            phases["initial"] - 5,
            phases["a92"] + 2,
            phases["b92"] - 2,
        ),
    }
    result["status"] = (
        "pass"
        if all(item["improved_by_at_least_5pct"] for item in result.values())
        else "fail"
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    args.output.write_text(rendered + "\n")
    print(rendered)
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
