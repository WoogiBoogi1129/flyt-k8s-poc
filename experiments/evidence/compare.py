#!/usr/bin/env python3
"""Compare complete loss/gradient/updated-parameter checkpoints; fail closed."""
import argparse
import math
from pathlib import Path
from evidence import digest, write_json


def compare(reference, candidate, atol, rtol):
    import torch
    if not math.isfinite(atol) or not math.isfinite(rtol) or atol < 0 or rtol < 0:
        raise ValueError("Tolerances must be finite and nonnegative")
    for key in ("fixture_sha256", "config_sha256", "batch", "seed", "model", "input_mode", "checkpoints", "device"):
        if reference["metadata"][key] != candidate["metadata"][key]:
            raise ValueError(f"Mismatched comparison input: {key}")
    for key in ("torch_version", "cuda_version", "source_sha256", "optimizer_execution", "disable_addmm_cuda_lt"):
        if reference["metadata"].get(key) != candidate["metadata"].get(key):
            raise ValueError(f"Mismatched execution configuration: {key}")
    expected = {str(x) for x in reference["metadata"]["checkpoints"]}
    if not expected or set(reference["checkpoints"]) != expected or set(candidate["checkpoints"]) != expected:
        raise ValueError("Missing/unexpected checkpoint")
    rows = []
    def tensor(name, ref, got):
        if not isinstance(ref, torch.Tensor) or not isinstance(got, torch.Tensor):
            rows.append({"name": name, "pass": False, "reason": "missing tensor/gradient"}); return
        if ref.shape != got.shape or ref.dtype != got.dtype or ref.numel() == 0:
            rows.append({"name": name, "pass": False, "reason": "shape/dtype/empty mismatch"}); return
        ref, got = ref.double(), got.double()
        finite = torch.isfinite(ref) & torch.isfinite(got)
        delta = (got - ref).abs()
        bad = ~finite | (delta > atol + rtol * ref.abs())
        valid = delta[finite]
        rel = delta[finite & (ref != 0)] / ref[finite & (ref != 0)].abs()
        rows.append({"name": name, "pass": not bool(bad.any()), "elements": ref.numel(),
            "exceeded": int(bad.sum()), "nonfinite": int((~finite).sum()),
            "max_absolute_error": float(valid.max()) if valid.numel() else None,
            "max_relative_error_nonzero_reference": float(rel.max()) if rel.numel() else None,
            "zero_reference_mismatches": int(((ref == 0) & (got != 0)).sum())})
    for step in sorted(expected, key=int):
        ref, got = reference["checkpoints"][step], candidate["checkpoints"][step]
        if set(ref) != {"loss", "gradients", "parameters"} or set(got) != set(ref):
            raise ValueError("Missing checkpoint category")
        if not ref["parameters"] or set(ref["gradients"]) != set(ref["parameters"]):
            raise ValueError("Reference gradient/parameter set mismatch")
        tensor(f"step{step}/loss", ref["loss"], got["loss"])
        for category in ("gradients", "parameters"):
            if set(ref[category]) != set(got[category]):
                raise ValueError(f"Missing/unexpected {category}")
            for key in sorted(ref[category]): tensor(f"step{step}/{category}/{key}", ref[category][key], got[category][key])
    return {"status": "PASS" if all(x["pass"] for x in rows) else "FAIL", "atol": atol, "rtol": rtol,
            "cpu_validation_only": reference["metadata"]["device"] == "cpu", "tensors": rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("reference", type=Path); p.add_argument("candidate", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--atol", type=float, default=1e-6); p.add_argument("--rtol", type=float, default=1e-4)
    a = p.parse_args()
    import torch
    try:
        result = compare(torch.load(a.reference, map_location="cpu", weights_only=True),
                         torch.load(a.candidate, map_location="cpu", weights_only=True), a.atol, a.rtol)
    except Exception as e:
        result = {"status": "FAIL", "reason": f"{type(e).__name__}: {e}"}
    result.update({"reference_sha256": digest(a.reference), "candidate_sha256": digest(a.candidate)})
    write_json(a.out, result)
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__": main()
