#!/usr/bin/env python3
"""Analysis of verified measurements, including heterogeneous VM slowdown."""
import argparse
import json
import math
from pathlib import Path
import statistics
from evidence import BACKENDS, write_json


def performance(records, repeats=5):
    groups = {}
    versions = set()
    for r in records:
        if r.get("status") != "PASS" or not r.get("correctness_pass") or r.get("device") != "cuda":
            raise ValueError("Performance input must be CUDA execution with verified correctness")
        for key in ("experiment_version", "config_sha256", "source_sha256", "gpu_uuid"):
            if not r.get(key): raise ValueError(f"Missing provenance {key}")
        versions.add(tuple(r[k] for k in ("experiment_version", "config_sha256", "source_sha256", "gpu_uuid")))
        if not math.isfinite(r["elapsed_seconds"]) or r["elapsed_seconds"] <= 0 or r["steps"] <= 0 or r["batch"] <= 0:
            raise ValueError("Invalid measurement")
        key = (r["batch"], r["input_mode"], r["backend"])
        groups.setdefault(key, []).append(r)
    if len(versions) != 1: raise ValueError("Cannot combine experiment versions/configurations/sources/GPUs")
    summaries = []
    conditions = {(k[0], k[1]) for k in groups}
    for batch, mode in sorted(conditions):
        stats = {}
        all_steps = set()
        for backend in BACKENDS:
            rows = groups.get((batch, mode, backend), [])
            if len(rows) != repeats or {r["repetition"] for r in rows} != set(range(1, repeats + 1)):
                raise ValueError("Incomplete or duplicate repetition block")
            times = [r["elapsed_seconds"] for r in rows]
            rates = [r["steps"] * batch / r["elapsed_seconds"] for r in rows]
            all_steps.update(r["steps"] for r in rows)
            stats[backend] = {"median_seconds": statistics.median(times), "min_seconds": min(times), "max_seconds": max(times),
                              "median_samples_per_second": statistics.median(rates), "min_samples_per_second": min(rates), "max_samples_per_second": max(rates)}
        if len(all_steps) != 1: raise ValueError("Unequal timed step counts")
        for backend, values in stats.items():
            values["passthrough_extra_time_fraction"] = values["median_seconds"] / stats["passthrough"]["median_seconds"] - 1
        summaries.append({"batch": batch, "input_mode": mode, "backends": stats,
                          "shm_over_rpc_throughput": stats["shm-hami"]["median_samples_per_second"] / stats["rpc-mps"]["median_samples_per_second"]})
    return summaries


def slowdown(solo_seconds, shared_seconds):
    if len(solo_seconds) != 2 or len(shared_seconds) != 2 or any(not math.isfinite(x) or x <= 0 for x in solo_seconds + shared_seconds):
        raise ValueError("Two positive solo/shared completion times required")
    ratios = [b / a for a, b in zip(solo_seconds, shared_seconds)]
    return {"vm_slowdown": ratios, "worst_slowdown": max(ratios)}


def common_window(streams, batch_sizes, start_bounds, seconds=60):
    """start_bounds are host-monotonic [GO sent, STARTED received] per VM.

    Count only completed chunks wholly inside a guaranteed shared window.
    No comparison of monotonic clock values from different machines.
    """
    if len(streams) != 2 or len(batch_sizes) != 2 or len(start_bounds) != 2:
        raise ValueError("Need two VM timelines")
    for stream, bounds in zip(streams, start_bounds):
        if not stream or bounds[1] < bounds[0]: raise ValueError("Empty timeline or invalid clock bounds")
        previous = 0
        for chunk in stream:
            if chunk["start_seconds"] != previous or chunk["end_seconds"] <= previous or chunk["steps"] <= 0:
                raise ValueError("Timeline must be contiguous, completed and ordered")
            previous = chunk["end_seconds"]
    start = max(b[1] for b in start_bounds)
    end = min(b[0] + s[-1]["end_seconds"] for b, s in zip(start_bounds, streams))
    if end - start < seconds: raise ValueError("Insufficient guaranteed overlap")
    left = start + (end - start - seconds) / 2
    right = left + seconds
    rows = []
    for stream, batch, bounds in zip(streams, batch_sizes, start_bounds):
        selected = [x for x in stream if bounds[0] + x["start_seconds"] >= left and bounds[1] + x["end_seconds"] <= right]
        samples = sum(x["steps"] for x in selected) * batch
        rows.append({"samples": samples, "samples_per_second_lower_bound": samples / seconds,
                     "completed_chunks": len(selected), "clock_uncertainty_seconds": bounds[1] - bounds[0]})
    return {"host_window": [left, right], "seconds": seconds, "vms": rows,
            "boundary_policy": "exclude partially overlapping chunks; report conservative throughput"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("records", type=Path); p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    write_json(a.out, performance(json.loads(a.records.read_text())))


if __name__ == "__main__": main()
