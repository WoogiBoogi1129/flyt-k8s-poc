#!/usr/bin/env python3
"""Evidence inventory and case ledger. Never treats missing measurements as PASS."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
BACKENDS = ("passthrough", "rpc-mps", "shm-hami")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def command(argv, timeout=30):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"argv": argv, "returncode": None, "stdout": "", "stderr": str(e)}


def source_snapshot():
    # Explicit allowlist: never traverse .local, evidence, secrets or environment files.
    files = {}
    for directory in ("runtime", "experiments", "scripts", "tests", "charts", "deploy"):
        for p in sorted((ROOT / directory).rglob("*")):
            if not p.is_file() or p.is_symlink() or any(x.startswith(".") or x == "__pycache__" for x in p.relative_to(ROOT).parts):
                continue
            if p.suffix not in {".py", ".c", ".h", ".cu", ".json", ".yaml", ".yml", ".sh", ".txt"}:
                continue
            if any(x in p.name.lower() for x in ("secret", "private", "credential")):
                continue
            files[str(p.relative_to(ROOT))] = digest(p)
    return {"commit": command(["git", "rev-parse", "HEAD"])["stdout"].strip(),
            "files": files, "sha256": canonical_hash(files)}


def matrix(c):
    cases = []
    def add(experiment, name, gates, **settings):
        cases.append({"id": f"{experiment}-{name}", "experiment": experiment,
                      "requires": gates, "settings": settings})
    for backend in BACKENDS:
        for seed in c["seeds"]:
            for batch in c["batches"]:
                for mode in c["input_modes"]:
                    add("E1", f"{backend}-{seed}-b{batch}-{mode}", ["runtime", "training", "baselines"],
                        backend=backend, seed=seed, batch=batch, input_mode=mode,
                        checkpoints=c["correctness_steps"], role="reference" if backend == "passthrough" else "candidate")
    for memory in c["memory_mib"]:
        for scenario in ("request_mapping", "below", "boundary", "over", "aggregate_race", "free_reallocate", "neighbor_continuity"):
            add("E2", f"m{memory}-{scenario}", ["runtime", "quota_contract"], memory_mib=memory, scenario=scenario)
    for compute in c["compute_percent"]:
        for rep in range(c["compute_repeats"]):
            add("E2", f"compute{compute}-r{rep + 1}", ["runtime", "compute_contract"], compute=compute, seconds=c["compute_seconds"])
    for rep in range(c["lifecycle_repeats"]):
        add("E2", f"lifecycle-r{rep + 1}", ["runtime", "release"], scenario="normal_reuse", repetition=rep + 1)
    rng = random.Random(c["order_seed"])
    for rep in range(c["performance_repeats"]):
        for batch in c["batches"]:
            for mode in c["input_modes"]:
                order = list(BACKENDS)
                rng.shuffle(order)
                for backend in order:
                    add("E3", f"{backend}-b{batch}-{mode}-r{rep + 1}",
                        ["runtime", "training", "baselines", "correctness", "calibration", "bindings"],
                        backend=backend, batch=batch, seed=c["seeds"][0], input_mode=mode, repetition=rep + 1, compute=100)
    for backend in BACKENDS[1:]:
        for combo, batches in (("same", [c["batches"][0]] * 2), ("mixed", c["batches"])):
            for rep in range(c["performance_repeats"]):
                for mode in ("correctness", "fixed", "window", "latency"):
                    add("E4", f"{backend}-{combo}-{mode}-r{rep + 1}",
                        ["runtime", "training", "baselines", "correctness", "calibration", "bindings", "sharing"],
                        backend=backend, batches=batches, seeds=c["seeds"], input_mode="resident", mode=mode,
                        compute_per_vm=50, repetition=rep + 1,
                        steps=c["shared_steps"], seconds=c["shared_seconds"], window_seconds=c["common_window_seconds"])
        for vm in (0, 1):
            for batch in c["batches"]:
                for rep in range(c["performance_repeats"]):
                    add("E4", f"{backend}-solo-vm{vm}-b{batch}-r{rep + 1}",
                        ["runtime", "training", "baselines", "correctness", "calibration", "bindings"],
                        backend=backend, vm=vm, seed=c["seeds"][vm], batch=batch,
                        input_mode="resident", mode="solo_fixed", compute=50, steps=c["shared_steps"], repetition=rep + 1)
    for scenario in ("prepare_cancel", "guest_kill", "vmi_stop", "vmi_force_delete", "worker_process_kill",
                     "worker_force_delete", "controller_restart", "vmi_recreate", "controller_api_disconnect"):
        for rep in range(c["fault_repeats"]):
            add("E5", f"{scenario}-r{rep + 1}", ["runtime", "training", "release", "bindings", "fault_fixture"], scenario=scenario, repetition=rep + 1)
    for scenario in c["conditional"]:
        add("E6" if scenario == "multi_gpu" else "E5", scenario, ["conditional"], scenario=scenario, conditional=True)
    return cases


def initial_gates():
    reasons = {
        "runtime": "Need real guest BAR round trip AND GPU kernel execution evidence; Worker Mapped alone is insufficient.",
        "training": "Need complete fixed MLP forward/backward/SGD compatibility evidence.",
        "baselines": "Need same-GPU passthrough and RPC/MPS guest executions with matched environment.",
        "release": "Need ordinary detach/reuse without a test-only finalizer.",
        "quota_contract": "Need frozen memory accounting, process/session scope and boundary rule.",
        "compute_contract": "Need installed HAMi limiter source/measurement interval and acceptance rule.",
        "correctness": "Need E1 results for every performance condition and both seeds.",
        "calibration": "Need all-backend pilot measurements and a frozen common step count.",
        "bindings": "Need measured VM/Worker CPU/NUMA placement, device ownership and immutable images.",
        "sharing": "Need two ready independent guest sessions on the same GPU and a barrier.",
        "fault_fixture": "Need isolated victim/neighbor fixtures and restoration paths.",
        "conditional": "Outside mandatory scope; requires separate readiness evidence."
    }
    return {k: {"status": "NOT_RUN", "reason": v, "evidence": []} for k, v in reasons.items()}


def preflight(out, c):
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "config.json", c)
    write_json(out / "source.json", source_snapshot())
    probes = {
        "nodes": ["kubectl", "get", "nodes", "-o", "json"],
        "gpus": ["nvidia-smi", "--query-gpu=index,name,uuid,pci.bus_id,memory.total,memory.used,driver_version", "--format=csv"],
        "gpu_processes": ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", "--format=csv"],
        "cpu_topology": ["lscpu", "-J"],
        "numa": ["nvidia-smi", "topo", "-m"],
        "pods": ["kubectl", "get", "pods", "-A", "-o", "json"],
        "vmis": ["kubectl", "get", "vmi", "-A", "-o", "json"],
        "channels": ["kubectl", "get", "flytsharedmemorychannels", "-A", "-o", "json"],
        "attachments": ["kubectl", "get", "flytchannelattachments", "-A", "-o", "json"],
    }
    raw = {}
    for name, argv in probes.items():
        value = command(argv)
        raw[name] = value
        if name in ("pods", "vmis") and value["returncode"] == 0:
            # Keep identities, placement, images and status. Never export Pod env or cloud-init userdata.
            obj = json.loads(value["stdout"])
            obj["items"] = [project_workload(x) for x in obj["items"]]
            value = {**value, "stdout": json.dumps(obj)}
        write_json(out / "preflight" / f"{name}.json", value)
    groups = []
    for p in sorted(Path("/sys/bus/pci/devices").glob("*")):
        if (p / "vendor").read_text().strip() != "0x10de":
            continue
        group = p / "iommu_group"
        groups.append({"bdf": p.name, "numa_node": (p / "numa_node").read_text().strip(),
                       "driver": (p / "driver").resolve().name if (p / "driver").exists() else None,
                       "iommu_group": group.resolve().name if group.exists() else None,
                       "group_devices": sorted(x.name for x in (group / "devices").glob("*"))})
    write_json(out / "preflight" / "pci.json", groups)
    gates = initial_gates()
    pods = json.loads(raw["pods"]["stdout"]).get("items", []) if raw["pods"]["returncode"] == 0 else []
    launchers = [p for p in pods if p["metadata"]["name"].startswith("virt-launcher-") and p.get("status", {}).get("phase") == "Running"]
    if launchers:
        p = sorted(launchers, key=lambda x: x["metadata"]["name"])[0]
        qemu = command(["kubectl", "exec", "-n", p["metadata"]["namespace"], p["metadata"]["name"],
                        "-c", "compute", "--", "/usr/libexec/qemu-kvm", "-device", "help"])
        write_json(out / "preflight" / "qemu.json", qemu)
        if qemu["returncode"] == 0 and 'name "ivshmem-plain"' not in qemu["stdout"]:
            gates["runtime"] = {"status": "BLOCKED", "reason": "Live launcher QEMU has no ivshmem-plain device.", "evidence": ["preflight/qemu.json"]}
    unsupported = ROOT / "runtime/shm/src/guest_compat.c"
    if "cudaLaunchKernel" in unsupported.read_text() and "fullPyTorch: NOT_IMPLEMENTED" in (ROOT / "versions.lock.yaml").read_text():
        gates["training"] = {"status": "BLOCKED", "reason": "Current source declares PyTorch unsupported; Runtime launch is rejected. No complete MLP execution evidence.", "evidence": ["source.json"]}
    write_json(out / "gates.json", gates)
    write_json(out / "matrix.json", matrix(c))
    write_json(out / "manifest.json", {"schema_version": 1, "stage": "predevelopment", "created_utc": datetime.now(timezone.utc).isoformat(),
                                      "experiment_version": c["experiment_version"], "config_sha256": canonical_hash(c),
                                      "gpu_uuid": c["gpu_uuid"], "namespace": c["namespace"], "formal_experiments_started": False})
    refresh(out)


def project_workload(obj):
    m, s = obj["metadata"], obj.get("spec", {})
    return {"kind": obj.get("kind"), "metadata": {k: m[k] for k in ("name", "namespace", "uid", "generation", "ownerReferences") if k in m},
            "spec": {"nodeName": s.get("nodeName"), "containers": [{"name": x["name"], "image": x["image"], "resources": x.get("resources", {})} for x in s.get("containers", [])],
                     "devices": s.get("domain", {}).get("devices", {}).get("gpus", [])}, "status": obj.get("status", {})}


def refresh(out):
    gates = json.loads((out / "gates.json").read_text())
    cases = json.loads((out / "matrix.json").read_text())
    for case in cases:
        d = out / "cases" / case["id"]
        if (d / "metrics.json").exists():
            continue  # Measured evidence is immutable; a new experiment needs a new directory.
        missing = [f"{g}: {gates[g]['reason']}" for g in case["requires"] if gates[g]["status"] != "PASS"]
        status = "NOT_RUN" if case["settings"].get("conditional") or not missing else "BLOCKED"
        write_json(d / "manifest.json", case)
        write_json(d / "metrics.json", {"status": status, "measured": False, "reason": missing})
    report(out)


def report(out):
    rows = []
    for p in sorted((out / "cases").glob("*/metrics.json")):
        m = json.loads(p.read_text())
        if m["status"] not in STATUSES:
            raise ValueError(f"Invalid status in {p}")
        rows.append({"id": p.parent.name, "status": m["status"], "measured": m.get("measured", False), "reason": m.get("reason", "")})
    write_json(out / "summary.json", {"counts": {s: sum(r["status"] == s for r in rows) for s in sorted(STATUSES)}, "cases": rows})
    lines = ["# SHM/HAMi 실증 상태", "", "이 표는 실행 증거의 상태이며, 차단된 실험의 성능값을 생성하지 않습니다.", "",
             "| 실험 | PASS | FAIL | BLOCKED | NOT_RUN |", "|---|---:|---:|---:|---:|"]
    for ex in ("E1", "E2", "E3", "E4", "E5", "E6"):
        subset = [r for r in rows if r["id"].startswith(ex + "-")]
        lines.append("| " + ex + " | " + " | ".join(str(sum(r["status"] == s for r in subset)) for s in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")) + " |")
    lines.extend(["", "## 선행 조건", ""])
    for gate, g in json.loads((out / "gates.json").read_text()).items():
        lines.append(f"- {gate}: **{g['status']}** — {g['reason']}")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")


def calibrate(c, pilots):
    expected = {(b, n, mode) for b in BACKENDS for n in c["batches"] for mode in c["input_modes"]}
    found = set()
    grouped = {}
    for p in pilots:
        key = (p["backend"], p["batch"], p["input_mode"])
        if key in found or key not in expected or p["status"] != "PASS":
            raise ValueError("Pilots must cover each backend/condition exactly once with successful measured execution")
        if not math.isfinite(p["elapsed_seconds"]) or p["elapsed_seconds"] <= 0 or type(p["steps"]) is not int or p["steps"] <= 0:
            raise ValueError("Invalid pilot time/step count")
        found.add(key)
        grouped.setdefault((p["batch"], p["input_mode"]), []).append(p["elapsed_seconds"] / p["steps"])
    if found != expected:
        raise ValueError("Missing baseline/condition pilot; cannot freeze")
    conditions = []
    for (batch, mode), seconds_per_step in sorted(grouped.items()):
        steps = math.ceil(c["minimum_measure_seconds"] / min(seconds_per_step))
        conditions.append({"batch": batch, "input_mode": mode, "steps": steps,
                           "timeout_seconds": max(c["timeouts"]["training_min"], math.ceil(c["timeouts"]["training_factor"] * (steps + c["warmup_steps"]) * max(seconds_per_step)))})
    return {"config_sha256": canonical_hash(c), "pilots_sha256": canonical_hash(pilots), "conditions": conditions}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=HERE / "config.json")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("preflight", "report"):
        s = sub.add_parser(name); s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("matrix"); s.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("calibrate"); s.add_argument("--pilots", type=Path, required=True); s.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    c = json.loads(a.config.read_text())
    if a.command == "preflight": preflight(a.out, c)
    elif a.command == "report": report(a.out)
    elif a.command == "matrix": write_json(a.out, matrix(c))
    elif a.command == "calibrate": write_json(a.out, calibrate(c, json.loads(a.pilots.read_text())))


if __name__ == "__main__":
    main()
