#!/usr/bin/env python3
"""Identical guest workload for all backends. CPU mode is harness validation only."""
import time
PROGRAM_START = time.monotonic()
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import traceback
from evidence import HERE, canonical_hash, digest, write_json


def load_torch():
    # Set before importing torch or creating a CUDA context.
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    return torch


def model(torch, c):
    a, b, d = c["model"]["widths"]
    return torch.nn.Sequential(torch.nn.Linear(a, b), torch.nn.ReLU(), torch.nn.Linear(b, d)).float()


def fixture(path, c, seed):
    torch = load_torch()
    torch.manual_seed(seed)
    net = model(torch, c)
    count = max(c["batches"])
    data = {"metadata": {"seed": seed, "model": c["model"], "max_batch": count},
            "weights": net.state_dict(), "x": torch.randn(count, c["model"]["widths"][0]),
            "y": torch.randn(count, c["model"]["widths"][-1])}
    if path.exists():
        raise ValueError("Refusing to overwrite fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(data, path)
    write_json(path.with_suffix(".json"), {**data["metadata"], "sha256": digest(path), "config_sha256": canonical_hash(c)})


def cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def execute(a, c):
    torch = load_torch()
    if a.device == "cuda" and (not torch.cuda.is_available() or torch.cuda.device_count() != 1):
        raise RuntimeError("Expected exactly one CUDA device; no CPU fallback")
    data = torch.load(a.fixture, map_location="cpu", weights_only=True)
    if data["metadata"]["model"] != c["model"] or a.batch > data["metadata"]["max_batch"]:
        raise ValueError("Fixture model/batch does not match configuration")
    net = model(torch, c).to(a.device)
    net.load_state_dict(data["weights"])
    optimizer = torch.optim.SGD(net.parameters(), lr=c["model"]["lr"], momentum=0, weight_decay=0)
    x_cpu, y_cpu = data["x"][:a.batch], data["y"][:a.batch]
    if a.input_mode == "resident":
        x, y = x_cpu.to(a.device), y_cpu.to(a.device)
    def sync():
        if a.device == "cuda": torch.cuda.synchronize()
    def step():
        nonlocal x, y
        if a.input_mode == "transfer":
            x, y = x_cpu.to(a.device), y_cpu.to(a.device)
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(net(x), y)
        loss.backward()
        optimizer.step()
        return loss
    # Warmup is excluded from correctness. Restore weights before measured training.
    if a.mode != "correctness":
        for _ in range(c["warmup_steps"]): step()
        sync()
        net.load_state_dict(data["weights"])
        optimizer.zero_grad(set_to_none=True)
    sync()
    if a.barrier:
        print(json.dumps({"event": "READY", "pid": os.getpid()}), flush=True)
        if sys.stdin.readline().strip() != "GO":
            raise RuntimeError("Start barrier missing/aborted")
    sync()
    t0, cpu0 = time.monotonic(), cpu_seconds()
    wall0 = time.time_ns()
    if a.barrier:
        print(json.dumps({"event": "STARTED"}), flush=True)
    checkpoints, intervals, latencies = {}, [], []
    n, last_n, last_t = 0, 0, 0.0
    count = max(c["correctness_steps"]) if a.mode == "correctness" else a.steps
    while True:
        before = time.monotonic() if a.mode == "latency" else None
        loss = step()
        n += 1
        if a.mode == "correctness" and n in c["correctness_steps"]:
            sync()
            checkpoints[str(n)] = {"loss": loss.detach().cpu().clone(),
                "gradients": {k: None if p.grad is None else p.grad.detach().cpu().clone() for k, p in net.named_parameters()},
                "parameters": {k: p.detach().cpu().clone() for k, p in net.named_parameters()}}
        if a.mode == "latency":
            sync()
            latencies.append(time.monotonic() - before)
        if a.mode == "window" and n % a.window_chunk_steps == 0:
            # Completion-delimited chunks. No interpolation of queued GPU work.
            sync()
            elapsed = time.monotonic() - t0
            intervals.append({"start_seconds": last_t, "end_seconds": elapsed, "steps": n - last_n})
            last_t, last_n = elapsed, n
            if elapsed >= a.seconds: break
        elif a.mode != "window" and n >= count:
            break
    sync()
    elapsed, used_cpu = time.monotonic() - t0, cpu_seconds() - cpu0
    metadata = {"backend": a.backend, "device": a.device, "mode": a.mode, "batch": a.batch,
        "seed": data["metadata"]["seed"], "fixture_sha256": digest(a.fixture), "config_sha256": canonical_hash(c),
        "torch_version": str(torch.__version__), "cuda_version": torch.version.cuda,
        "input_mode": a.input_mode, "model": c["model"], "checkpoints": c["correctness_steps"],
        "measurement_start_wall_ns": wall0, "measurement_start_monotonic": t0,
        "cpu_affinity": sorted(os.sched_getaffinity(0)), "source_sha256": digest(Path(__file__)),
        "device_name": torch.cuda.get_device_name(0) if a.device == "cuda" else "cpu"}
    write_json(a.out / "manifest.json", metadata)
    if checkpoints:
        torch.save({"metadata": metadata, "checkpoints": checkpoints}, a.out / "tensors.pt")
    metrics = {"status": "PASS", "measured": True, "formal_result": False,
        "scope": "guest workload execution only; correctness, route, host CPU and gates require separate evidence",
        "cpu_validation_only": a.device == "cpu", "steps": n, "elapsed_seconds": elapsed,
        "samples_per_second": n * a.batch / elapsed, "guest_process_cpu_seconds": used_cpu,
        "guest_process_average_cores": used_cpu / elapsed,
        "program_seconds_before_artifact_write": time.monotonic() - PROGRAM_START}
    if latencies:
        values = sorted(latencies)
        import math
        metrics.update({"latency_p50_seconds": values[math.ceil(len(values) * .50) - 1],
                        "latency_p95_seconds": values[math.ceil(len(values) * .95) - 1]})
        write_json(a.out / "latencies.json", latencies)
    if intervals: write_json(a.out / "intervals.json", intervals)
    write_json(a.out / "metrics.json", metrics)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=HERE / "config.json")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("fixture"); s.add_argument("--out", type=Path, required=True); s.add_argument("--seed", type=int, choices=(2026, 2027), required=True)
    s = sub.add_parser("run")
    s.add_argument("--out", type=Path, required=True); s.add_argument("--fixture", type=Path, required=True)
    s.add_argument("--backend", choices=("passthrough", "rpc-mps", "shm-hami"), required=True)
    s.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    s.add_argument("--mode", choices=("correctness", "fixed", "window", "latency"), required=True)
    s.add_argument("--input-mode", choices=("resident", "transfer"), required=True)
    s.add_argument("--batch", type=int, required=True); s.add_argument("--steps", type=int, default=1000)
    s.add_argument("--seconds", type=float, default=90); s.add_argument("--window-chunk-steps", type=int, default=10)
    s.add_argument("--barrier", action="store_true")
    a = p.parse_args()
    c = json.loads(a.config.read_text())
    if a.command == "fixture": fixture(a.out, c, a.seed); return
    if a.batch <= 0 or a.steps <= 0 or a.seconds <= 0 or a.window_chunk_steps <= 0:
        p.error("batch/steps/seconds/chunk must be positive")
    a.out.mkdir(parents=True, exist_ok=False)
    try:
        execute(a, c)
    except Exception as e:
        write_json(a.out / "metrics.json", {"status": "FAIL", "measured": False, "formal_result": False,
                                          "reason": f"{type(e).__name__}: {e}"})
        (a.out / "error.txt").write_text(traceback.format_exc())
        raise


if __name__ == "__main__": main()
