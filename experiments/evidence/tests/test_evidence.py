import copy
import json
import math
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evidence import HERE, BACKENDS, matrix, calibrate, project_workload
from analyze import performance, slowdown, common_window


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((HERE / "config.json").read_text())

    def test_matrix_complete_blocks_unique_and_reproducible(self):
        rows = matrix(self.c)
        self.assertEqual(rows, matrix(self.c))
        self.assertEqual(len(rows), len({r["id"] for r in rows}))
        e3 = [r for r in rows if r["experiment"] == "E3"]
        self.assertEqual(len(e3), 60)
        for batch in self.c["batches"]:
            for mode in self.c["input_modes"]:
                for rep in range(1, 6):
                    group = [r for r in e3 if r["settings"]["batch"] == batch and r["settings"]["input_mode"] == mode and r["settings"]["repetition"] == rep]
                    self.assertEqual({r["settings"]["backend"] for r in group}, set(BACKENDS))

    def pilots(self):
        return [{"backend": backend, "batch": batch, "input_mode": mode, "status": "PASS", "steps": 10, "elapsed_seconds": elapsed}
                for backend, elapsed in zip(BACKENDS, [1, 2, 3]) for batch in self.c["batches"] for mode in self.c["input_modes"]]

    def test_calibration_fastest_gets_thirty_seconds(self):
        plan = calibrate(self.c, self.pilots())
        self.assertTrue(all(x["steps"] == 300 for x in plan["conditions"]))
        self.assertTrue(all(x["timeout_seconds"] >= 525 for x in plan["conditions"]))

    def test_calibration_rejects_incomplete_duplicate_and_nan(self):
        for values in (self.pilots()[:-1], self.pilots() + [self.pilots()[0]]):
            with self.assertRaises(ValueError): calibrate(self.c, values)
        values = self.pilots(); values[0]["elapsed_seconds"] = math.nan
        with self.assertRaises(ValueError): calibrate(self.c, values)

    def test_inventory_excludes_env_and_cloud_init(self):
        pod = {"metadata": {"name": "x", "annotations": {"secret": "do-not-export"}}, "spec": {"containers": [{"name": "x", "image": "x", "env": [{"value": "do-not-export"}]}], "volumes": [{"cloudInitNoCloud": {"userData": "do-not-export"}}]}}
        self.assertNotIn("do-not-export", json.dumps(project_workload(pod)))

    def records(self):
        return [{"backend": b, "batch": 32, "input_mode": "resident", "repetition": r,
                 "status": "PASS", "correctness_pass": True, "device": "cuda", "experiment_version": "v1",
                 "config_sha256": "c", "source_sha256": "s", "gpu_uuid": "g", "elapsed_seconds": t, "steps": 100}
                for b, t in zip(BACKENDS, [1., 2., 1.5]) for r in range(1, 6)]

    def test_performance_ratios(self):
        result = performance(self.records())[0]
        self.assertAlmostEqual(result["backends"]["shm-hami"]["passthrough_extra_time_fraction"], .5)
        self.assertAlmostEqual(result["shm_over_rpc_throughput"], 4 / 3)

    def test_no_wrong_calculation_or_mixed_revision_performance(self):
        for key, value in (("correctness_pass", False), ("device", "cpu"), ("source_sha256", "different"), ("steps", 50)):
            rows = self.records(); rows[0][key] = value
            with self.assertRaises(ValueError): performance(rows)
        with self.assertRaises(ValueError): performance(self.records()[:-1])

    def test_shared_window_handles_clock_uncertainty(self):
        stream = [{"start_seconds": i, "end_seconds": i + 1, "steps": 10} for i in range(90)]
        result = common_window([stream, stream], [32, 1024], [[1000, 1000.01], [1000.02, 1000.03]])
        self.assertEqual(result["seconds"], 60)
        self.assertLessEqual(result["vms"][0]["samples_per_second_lower_bound"], 320)
        self.assertGreater(result["vms"][0]["samples_per_second_lower_bound"], 300)
        with self.assertRaises(ValueError): common_window([stream[:30], stream], [32, 32], [[0, .1], [0, .1]])

    def test_slowdown_is_normalized_per_vm(self):
        self.assertEqual(slowdown([10, 100], [20, 150]), {"vm_slowdown": [2, 1.5], "worst_slowdown": 2})


try:
    import torch
except ImportError:
    torch = None


@unittest.skipIf(torch is None, "torch required for tensor comparator tests")
class ComparatorTests(unittest.TestCase):
    def setUp(self):
        from compare import compare
        self.compare = compare
        self.ref = {"metadata": {"fixture_sha256": "f", "config_sha256": "c", "batch": 1, "seed": 2026,
                   "model": "mlp", "input_mode": "resident", "checkpoints": [1], "device": "cpu"},
                    "checkpoints": {"1": {"loss": torch.tensor(1.), "parameters": {"w": torch.tensor([0., 1.])}, "gradients": {"w": torch.tensor([0., .5])}}}}

    def test_exact_match(self):
        self.assertEqual(self.compare(self.ref, copy.deepcopy(self.ref), 1e-6, 1e-4)["status"], "PASS")

    def test_changed_execution_configuration_rejected(self):
        for key,value in (("optimizer_execution", {"foreach": True}),
                          ("disable_addmm_cuda_lt", True), ("torch_version", "different"),
                          ("cuda_version", "different"), ("source_sha256", "different")):
            candidate=copy.deepcopy(self.ref);candidate["metadata"][key]=value
            with self.assertRaises(ValueError): self.compare(self.ref,candidate,1e-6,1e-4)

    def test_nan_and_gradient_corruption_fail(self):
        for value in (float("nan"), .6):
            candidate = copy.deepcopy(self.ref); candidate["checkpoints"]["1"]["gradients"]["w"][1] = value
            self.assertEqual(self.compare(self.ref, candidate, 1e-6, 1e-4)["status"], "FAIL")

    def test_missing_gradient_fails(self):
        candidate = copy.deepcopy(self.ref); candidate["checkpoints"]["1"]["gradients"]["w"] = None
        self.assertEqual(self.compare(self.ref, candidate, 1e-6, 1e-4)["status"], "FAIL")

    def test_missing_checkpoint_and_different_fixture_rejected(self):
        candidate = copy.deepcopy(self.ref); candidate["checkpoints"] = {}
        with self.assertRaises(ValueError): self.compare(self.ref, candidate, 1e-6, 1e-4)
        candidate = copy.deepcopy(self.ref); candidate["metadata"]["fixture_sha256"] = "different"
        with self.assertRaises(ValueError): self.compare(self.ref, candidate, 1e-6, 1e-4)

    def test_nonfinite_tolerance_cannot_hide_failure(self):
        for atol, rtol in ((math.nan, 1e-4), (1e-6, math.inf), (-1, 0)):
            with self.assertRaises(ValueError): self.compare(self.ref, self.ref, atol, rtol)

    def test_cli_checkpoint_weights_only_roundtrip_and_timing_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c = json.loads((HERE / "config.json").read_text())
            c["model"]["widths"] = [2, 3, 2]
            c["batches"] = [2, 4]; c["correctness_steps"] = [1, 3]; c["warmup_steps"] = 2
            config = root / "config.json"; config.write_text(json.dumps(c))
            base = [sys.executable, str(HERE / "train.py"), "--config", str(config)]
            def call(args):
                subprocess.run(base + args, check=True, capture_output=True, text=True, timeout=30)
            data = root / "fixture.pt"
            call(["fixture", "--seed", "2026", "--out", str(data)])
            tensors = []
            for i, (mode, input_mode) in enumerate((("correctness", "resident"), ("correctness", "transfer"), ("fixed", "transfer"), ("window", "resident"), ("latency", "resident"))):
                out = root / str(i)
                call(["run", "--fixture", str(data), "--backend", "shm-hami", "--device", "cpu", "--mode", mode,
                      "--batch", "2", "--input-mode", input_mode, "--steps", "3", "--seconds", ".05", "--out", str(out)])
                metrics = json.loads((out / "metrics.json").read_text())
                self.assertEqual(metrics["status"], "PASS")
                self.assertTrue(metrics["cpu_validation_only"])
                self.assertFalse(metrics["formal_result"])
                if mode == "correctness":
                    value = torch.load(out / "tensors.pt", map_location="cpu", weights_only=True)
                    self.assertIs(type(value["metadata"]["torch_version"]), str)
                    tensors.append(value)
                elif mode == "window": self.assertTrue((out / "intervals.json").exists())
                elif mode == "latency": self.assertIn("latency_p95_seconds", metrics)
            # Transfer changes measurement semantics, so comparator correctly refuses cross-mode inputs.
            with self.assertRaises(ValueError): self.compare(tensors[0], tensors[1], 1e-6, 1e-4)


class PairTests(unittest.TestCase):
    def test_barrier_clock_bounds_and_failure_cancellation(self):
        import asyncio
        from pair import run
        normal = "import json,sys; print(json.dumps({'event':'READY'}),flush=True); assert input()=='GO'; print(json.dumps({'event':'STARTED'}),flush=True)"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cancel = root / "cancelled"
            cancellation = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('cancelled')", str(cancel)]
            clients = [{"argv": [sys.executable, "-c", normal], "cancel_argv": cancellation} for _ in range(2)]
            spec = {"clients": clients, "start_timeout": 3, "training_timeout": 3}
            result = asyncio.run(run(spec, root / "ok"))
            self.assertEqual(result["status"], "PASS")
            self.assertFalse(cancel.exists())
            self.assertTrue(all(b >= a for a, b in result["start_bounds"]))
            spec["clients"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(1)"]
            result = asyncio.run(run(spec, root / "fail"))
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(cancel.exists())


if __name__ == "__main__": unittest.main()
