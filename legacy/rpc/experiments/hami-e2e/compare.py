#!/usr/bin/env python3
"""Offline two-quota compute analysis. No cluster connection or workload launch."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from run import CASES, Blocked, need, save


def read(path):
    data = json.loads(path.read_text())
    need(data.get("schema") == 1 and data.get("stage") == "06-hami-e2e", "wrong report schema")
    need("reason" not in data and data.get("target_after") == data.get("target"), "run identity was not stable")
    return data


def samples(report, case):
    c = report["cases"][case]
    need(c["status"] == "PASS", "compute workload did not pass: " + case)
    values = []
    for s in c["samples"]:
        need(s["status"] == "PASS" and s["exit_code"] == 0, "compute sample failed")
        events = [e for e in s["events"] if e.get("event") == "WORKLOAD" and e.get("run_id") == s["run_id"]]
        need(len(events) == 1, "missing/ambiguous workload measurement")
        e = events[0]
        need(e["elapsed_seconds"] >= report["inputs"]["seconds"] and e["launches"] > 0, "measurement too short")
        value = e["launches"] / e["elapsed_seconds"]
        need(math.isfinite(value) and value > 0, "invalid throughput")
        values.append(value)
    need(len(values) == report["inputs"]["repeats"] and len(values) >= 3, "insufficient repeats")
    median = statistics.median(values)
    return dict(values=values, median=median, relative_range=(max(values)-min(values))/median)


def hami_digests(report_path):
    # Use evidence from guest RPC processes rather than guessing the core version
    # from a chart tag. The binary mounted by the device plugin may differ.
    values = set()
    for path in report_path.parent.glob("*/*.inventory.json"):
        data = json.loads(path.read_text())
        for rpc in data["rpc"]:
            values.update(rpc["hami_sha256"].values())
    need(len(values) == 1, "exactly one observed HAMi binary digest required per run")
    return sorted(values)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low", required=True, type=Path)
    p.add_argument("--high", required=True, type=Path)
    p.add_argument("--max-throughput-ratio", required=True, type=float,
                   help="Predeclared low/high median threshold; do not fit it to observed results")
    p.add_argument("--max-relative-range", type=float, default=0.30)
    p.add_argument("--conditions", required=True, type=Path,
                   help="Explicit environment observations; this program cannot establish exclusive GPU access")
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    need(not a.output.exists(), "output already exists")
    result = dict(schema=1, status="BLOCKED", compute_enforcement={"status": "NOT_RUN"},
                  scope="stage-6 tested runtime/driver allocation and launch paths; not all CUDA APIs or multi-VM isolation")
    try:
        need(0 < a.max_throughput_ratio < 1 and 0 < a.max_relative_range < 1, "invalid comparison thresholds")
        low, high = read(a.low), read(a.high)
        l, h = low["target"], high["target"]
        for key in ("gpu_uuid", "node", "worker_image", "image_id", "manager_image"):
            need(l[key] == h[key], "comparison environment differs: " + key)
        for key in ("probe_sha256", "ptx_sha256", "guest_client_sha256", "chunk_mib", "headroom_mib", "seconds", "repeats", "experiment_label"):
            need(low["inputs"][key] == high["inputs"][key], "comparison inputs differ: " + key)
        need(l["quota"]["count"] == h["quota"]["count"] == 1 and l["quota"]["memoryMiB"] == h["quota"]["memoryMiB"], "memory/count differ")
        need(1 <= l["quota"]["compute"] < h["quota"]["compute"] <= 100, "expected increasing core quota")
        need(l["vmi_uid"] != h["vmi_uid"] and l["pod_uid"] != h["pod_uid"], "quota changes require new VMI/Pod identities")
        need(hami_digests(a.low) == hami_digests(a.high), "HAMi binary differs")
        condition = json.loads(a.conditions.read_text())
        hashes = {"low_report_sha256": hashlib.sha256(a.low.read_bytes()).hexdigest(),
                  "high_report_sha256": hashlib.sha256(a.high.read_bytes()).hexdigest()}
        need(all(condition.get(k) == v for k, v in hashes.items()), "conditions do not refer to these reports")
        for key in ("same_driver", "no_other_gpu_workload", "no_tracer_during_compute", "no_power_or_clock_change"):
            need(condition.get(key) is True, "environment observation missing: " + key)
        need(condition.get("evidence_paths") and all(Path(v).is_file() for v in condition["evidence_paths"]), "environment evidence files required")
        result["conditions"] = dict(observations=condition, source="operator supplied; not independently measured by comparator",
                                    evidence_sha256={v: hashlib.sha256(Path(v).read_bytes()).hexdigest() for v in condition["evidence_paths"]})
        result["source_reports"] = hashes
        result["thresholds"] = dict(max_throughput_ratio=a.max_throughput_ratio, max_relative_range=a.max_relative_range)
        measurements = {}
        passed = True
        for case in ("compute-runtime", "standalone-compute"):
            ls, hs = samples(low, case), samples(high, case)
            need(max(ls["relative_range"], hs["relative_range"]) <= a.max_relative_range, "compute results too variable: " + case)
            ratio = ls["median"] / hs["median"]
            passed &= ratio <= a.max_throughput_ratio
            measurements[case] = dict(low=ls, high=hs, low_high_ratio=ratio)
        result["compute_enforcement"] = dict(status="PASS" if passed else "FAIL", measurements=measurements,
                                              note="Workload-specific observed throttling; not a linear quota/performance guarantee")
        statuses = [report["cases"][name]["status"] for report in (low, high) for name in CASES]
        result["case_statuses"] = {"low": {k: v["status"] for k, v in low["cases"].items()},
                                  "high": {k: v["status"] for k, v in high["cases"].items()}}
        result["status"] = "FAIL" if not passed or "FAIL" in statuses else "PASS" if all(s == "PASS" for s in statuses) else "BLOCKED"
    except Exception as e:
        result["reason"] = str(e)
    save(a.output, result)
    print(json.dumps({"status": result["status"], "report": str(a.output)}))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
