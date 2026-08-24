#!/usr/bin/env python3
"""Bounded PyTorch/Flyt compatibility matrix for the single-GPU PoC."""

import argparse
import gc
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import torch


CORE_TESTS = {
    "environment",
    "tensor_runtime",
    "pinned_memory",
    "autograd_optimizer",
    "cublas",
    "cudnn",
    "stream_event",
    "cuda_graph",
    "amp",
    "allocator",
    "models",
    "sdpa",
    "serialization",
    "rng",
    "linalg",
    "fft",
    "sparse",
}


def close(actual, expected, rtol=1e-4, atol=1e-5):
    torch.testing.assert_close(actual, expected, rtol=rtol, atol=atol)


def environment():
    assert torch.cuda.is_available()
    assert torch.cuda.device_count() == 1
    props = torch.cuda.get_device_properties(0)
    assert props.major == 12 and props.minor == 0
    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(0),
        "capability": list(torch.cuda.get_device_capability(0)),
        "arch_list": torch.cuda.get_arch_list(),
        "multiprocessor_count": props.multi_processor_count,
        "total_memory": props.total_memory,
    }


def tensor_runtime():
    torch.manual_seed(7)
    cpu = torch.randn(128, 64)
    gpu = cpu.cuda()
    result = ((gpu.transpose(0, 1).contiguous() + 2.0) * 0.5).sum(dim=1)
    expected = ((cpu.transpose(0, 1).contiguous() + 2.0) * 0.5).sum(dim=1)
    close(result.cpu(), expected)
    indexed = gpu[:, ::2].clone().cpu()
    close(indexed, cpu[:, ::2])
    return {"checksum": float(result.sum().cpu())}


def pinned_memory():
    pinned = torch.empty((64, 64), pin_memory=True).normal_()
    pinned_roundtrip = pinned.to("cuda", non_blocking=True).to(
        "cpu", non_blocking=True
    )
    torch.cuda.synchronize()
    close(pinned_roundtrip, pinned)
    return {
        "pinned_memory": pinned.is_pinned(),
    }


def autograd_optimizer():
    torch.manual_seed(11)
    model = torch.nn.Sequential(
        torch.nn.Linear(32, 64), torch.nn.ReLU(), torch.nn.Linear(64, 8)
    ).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
    x = torch.randn(64, 32, device="cuda")
    target = torch.randn(64, 8, device="cuda")
    losses = []
    for _ in range(20):
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(model(x), target)
        assert torch.isfinite(loss)
        loss.backward()
        assert all(p.grad is not None for p in model.parameters())
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
    assert losses[-1] < losses[0]
    return {"loss_first": losses[0], "loss_last": losses[-1]}


def cublas():
    torch.manual_seed(13)
    a_cpu = torch.randn(256, 384)
    b_cpu = torch.randn(384, 192)
    expected = a_cpu @ b_cpu
    actual = a_cpu.cuda() @ b_cpu.cuda()
    close(actual.cpu(), expected, rtol=2e-4, atol=2e-4)
    batched = torch.bmm(
        torch.randn(8, 32, 48, device="cuda"),
        torch.randn(8, 48, 24, device="cuda"),
    )
    assert torch.isfinite(batched).all()
    return {"checksum": float(actual.sum().cpu())}


def cudnn():
    assert torch.backends.cudnn.is_available()
    torch.manual_seed(17)
    conv = torch.nn.Conv2d(3, 16, 3, padding=1).cuda()
    bn = torch.nn.BatchNorm2d(16).cuda()
    x = torch.randn(8, 3, 32, 32, device="cuda", requires_grad=True)
    y = torch.nn.functional.max_pool2d(torch.relu(bn(conv(x))), 2)
    loss = y.square().mean()
    loss.backward()
    assert x.grad is not None and torch.isfinite(x.grad).all()
    return {
        "cudnn_version": torch.backends.cudnn.version(),
        "loss": float(loss.detach().cpu()),
    }


def stream_event():
    stream = torch.cuda.Stream()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    with torch.cuda.stream(stream):
        start.record(stream)
        x = torch.randn(512, 512, device="cuda")
        y = x @ x
        end.record(stream)
    stream.synchronize()
    assert torch.isfinite(y).all()
    return {"elapsed_ms": start.elapsed_time(end)}


def cuda_graph():
    torch.manual_seed(18)
    static_a = torch.randn(64, 64, device="cuda")
    static_b = torch.randn(64, 64, device="cuda")
    graph = torch.cuda.CUDAGraph()
    torch.cuda.synchronize()
    with torch.cuda.graph(graph):
        static_output = torch.relu(static_a @ static_b)
    graph.replay()
    first = static_output.clone()
    close(first, torch.relu(static_a @ static_b))
    static_a.copy_(torch.eye(64, device="cuda"))
    graph.replay()
    second = static_output.clone()
    close(second, torch.relu(static_b))
    return {
        "first_checksum": float(first.sum().cpu()),
        "second_checksum": float(second.sum().cpu()),
    }


def amp():
    results = {}
    for dtype, tolerance in ((torch.float16, (1e-2, 1e-2)), (torch.bfloat16, (3e-2, 3e-2))):
        torch.manual_seed(19)
        model = torch.nn.Linear(64, 32).cuda()
        x = torch.randn(16, 64, device="cuda")
        with torch.autocast(device_type="cuda", dtype=dtype):
            y = model(x)
            loss = y.square().mean()
        loss.backward()
        assert torch.isfinite(loss)
        reference = torch.nn.functional.linear(x.float(), model.weight.float(), model.bias.float())
        close(y.float(), reference, rtol=tolerance[0], atol=tolerance[1])
        results[str(dtype)] = float(loss.detach().cpu())
    return results


def allocator():
    torch.cuda.empty_cache()
    before = torch.cuda.memory_allocated()
    blocks = [torch.empty(8 * 1024 * 1024, dtype=torch.uint8, device="cuda") for _ in range(16)]
    allocated = torch.cuda.memory_allocated()
    assert allocated > before
    del blocks
    gc.collect()
    torch.cuda.synchronize()
    torch.cuda.empty_cache()
    after = torch.cuda.memory_allocated()
    assert after <= before
    return {"before": before, "peak_sample": allocated, "after": after}


def allocator_cuda_malloc_async():
    environment = os.environ.copy()
    environment["PYTORCH_CUDA_ALLOC_CONF"] = "backend:cudaMallocAsync"
    code = """
import json
import torch
x = torch.empty(64 * 1024 * 1024, dtype=torch.uint8, device='cuda')
x.fill_(3)
torch.cuda.synchronize()
print(json.dumps({'checksum': int(x[:1024].sum().cpu())}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "cudaMallocAsync allocator subprocess failed: "
            f"stdout={completed.stdout[-2000:]!r} stderr={completed.stderr[-4000:]!r}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def models():
    torch.manual_seed(23)
    cnn = torch.nn.Sequential(
        torch.nn.Conv2d(3, 16, 3, padding=1),
        torch.nn.ReLU(),
        torch.nn.AdaptiveAvgPool2d(1),
        torch.nn.Flatten(),
        torch.nn.Linear(16, 10),
    ).cuda()
    optimizer = torch.optim.SGD(cnn.parameters(), lr=0.05)
    first = last = None
    for _ in range(20):
        x = torch.randn(16, 3, 32, 32, device="cuda")
        target = torch.randint(0, 10, (16,), device="cuda")
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.cross_entropy(cnn(x), target)
        loss.backward()
        optimizer.step()
        first = float(loss.detach().cpu()) if first is None else first
        last = float(loss.detach().cpu())

    encoder = torch.nn.TransformerEncoder(
        torch.nn.TransformerEncoderLayer(
            d_model=64, nhead=8, dim_feedforward=128, batch_first=True
        ),
        num_layers=2,
    ).cuda()
    transformer_input = torch.randn(4, 32, 64, device="cuda", requires_grad=True)
    transformer_out = encoder(transformer_input)
    transformer_out.square().mean().backward()
    assert transformer_input.grad is not None

    torchvision_status = "not-installed"
    try:
        from torchvision.models import resnet18

        resnet = resnet18(weights=None, num_classes=10).cuda()
        rx = torch.randn(2, 3, 64, 64, device="cuda")
        rloss = resnet(rx).square().mean()
        rloss.backward()
        torchvision_status = "resnet18-forward-backward-pass"
    except ImportError:
        pass

    return {
        "cnn_loss_first": first,
        "cnn_loss_last": last,
        "transformer_checksum": float(transformer_out.sum().detach().cpu()),
        "torchvision": torchvision_status,
    }


def sdpa():
    torch.manual_seed(27)
    query = torch.randn(2, 8, 32, 64, device="cuda", requires_grad=True)
    key = torch.randn(2, 8, 32, 64, device="cuda", requires_grad=True)
    value = torch.randn(2, 8, 32, 64, device="cuda", requires_grad=True)
    output = torch.nn.functional.scaled_dot_product_attention(
        query, key, value, dropout_p=0.0, is_causal=True
    )
    output.square().mean().backward()
    assert all(tensor.grad is not None for tensor in (query, key, value))
    assert torch.isfinite(output).all()
    return {"checksum": float(output.sum().detach().cpu())}


def serialization():
    torch.manual_seed(29)
    model = torch.nn.Linear(8, 4).cuda()
    sample = torch.randn(2, 8, device="cuda")
    expected = model(sample).detach().cpu()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "checkpoint.pt"
        torch.save({"model": model.state_dict(), "sample": sample.cpu()}, path)
        restored = torch.nn.Linear(8, 4).cuda()
        state = torch.load(path, map_location="cpu", weights_only=True)
        restored.load_state_dict(state["model"])
        actual = restored(state["sample"].cuda()).detach().cpu()
    close(actual, expected)
    return {"checksum": float(actual.sum())}


def rng():
    torch.cuda.manual_seed_all(31)
    uniform = torch.rand(4096, device="cuda")
    normal = torch.randn(4096, device="cuda")
    assert 0.45 < float(uniform.mean().cpu()) < 0.55
    assert abs(float(normal.mean().cpu())) < 0.1
    assert 0.8 < float(normal.var().cpu()) < 1.2
    return {
        "uniform_mean": float(uniform.mean().cpu()),
        "normal_mean": float(normal.mean().cpu()),
    }


def linalg():
    torch.manual_seed(37)
    matrix_cpu = torch.randn(64, 64)
    matrix_cpu = matrix_cpu @ matrix_cpu.T + torch.eye(64) * 0.1
    vector_cpu = torch.randn(64, 4)
    actual = torch.linalg.solve(matrix_cpu.cuda(), vector_cpu.cuda()).cpu()
    residual = float((matrix_cpu @ actual - vector_cpu).abs().max())
    assert residual < 5e-3
    return {"residual": residual}


def fft():
    torch.manual_seed(41)
    signal_cpu = torch.randn(32, 1024)
    expected = torch.fft.rfft(signal_cpu)
    actual = torch.fft.rfft(signal_cpu.cuda()).cpu()
    close(actual, expected, rtol=2e-3, atol=2e-3)
    return {"checksum": float(actual.abs().sum())}


def sparse():
    indices = torch.tensor([[0, 0, 1, 2], [0, 2, 1, 2]], device="cuda")
    values = torch.tensor([1.0, 2.0, 3.0, 4.0], device="cuda")
    sparse_matrix = torch.sparse_coo_tensor(indices, values, (3, 3)).coalesce()
    dense = torch.arange(12, dtype=torch.float32, device="cuda").reshape(3, 4)
    actual = torch.sparse.mm(sparse_matrix, dense).cpu()
    expected = torch.tensor(
        [[16.0, 19.0, 22.0, 25.0],
         [12.0, 15.0, 18.0, 21.0],
         [32.0, 36.0, 40.0, 44.0]]
    )
    close(actual, expected)
    return {"checksum": float(actual.sum())}


def compile_modes():
    if not hasattr(torch, "compile"):
        return {"available": False}

    def fn(x, y):
        return torch.relu(x @ y)

    x = torch.randn(32, 32, device="cuda")
    y = torch.randn(32, 32, device="cuda")
    result = {}
    expected = fn(x, y)
    for backend in ("eager", "inductor"):
        try:
            compiled = torch.compile(fn, backend=backend)
            actual = compiled(x, y)
            close(actual, expected)
            result[backend] = "pass"
        except Exception as error:  # optional capability classification
            result[backend] = f"unsupported: {type(error).__name__}: {error}"
    unsupported = {key: value for key, value in result.items() if value != "pass"}
    if unsupported:
        raise RuntimeError(json.dumps({"results": result}, sort_keys=True))
    return result


TESTS = {
    "environment": environment,
    "tensor_runtime": tensor_runtime,
    "pinned_memory": pinned_memory,
    "autograd_optimizer": autograd_optimizer,
    "cublas": cublas,
    "cudnn": cudnn,
    "stream_event": stream_event,
    "cuda_graph": cuda_graph,
    "amp": amp,
    "allocator": allocator,
    "allocator_cuda_malloc_async": allocator_cuda_malloc_async,
    "models": models,
    "sdpa": sdpa,
    "serialization": serialization,
    "rng": rng,
    "linalg": linalg,
    "fft": fft,
    "sparse": sparse,
    "compile_modes": compile_modes,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--tests",
        help="comma-separated test names; defaults to the full matrix",
    )
    args = parser.parse_args()
    selected_names = list(TESTS)
    if args.tests:
        selected_names = [name.strip() for name in args.tests.split(",") if name.strip()]
        unknown = sorted(set(selected_names) - TESTS.keys())
        if unknown:
            parser.error(f"unknown tests: {', '.join(unknown)}")
    results = {
        "started_at": time.time(),
        "pid": os.getpid(),
        "selected_tests": selected_names,
        "tests": {},
    }
    core_failed = False
    for name in selected_names:
        test = TESTS[name]
        started = time.monotonic()
        try:
            # A failed CUDA call can leave a process-local last-error value.
            # Clear it so one unsupported capability does not contaminate the
            # result of the next independently classified test.
            try:
                torch.cuda.cudart().cudaGetLastError()
            except Exception:
                pass
            details = test()
            status = "pass"
        except Exception as error:
            details = {
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc(),
            }
            status = "fail"
            if name in CORE_TESTS:
                core_failed = True
            try:
                torch.cuda.cudart().cudaGetLastError()
            except Exception:
                pass
        results["tests"][name] = {
            "status": status,
            "duration_s": time.monotonic() - started,
            "details": details,
        }
        print(json.dumps({name: results["tests"][name]}, sort_keys=True), flush=True)
    results["finished_at"] = time.time()
    results["core_status"] = "fail" if core_failed else "pass"
    results["extended_status"] = (
        "fail"
        if any(item["status"] != "pass" for item in results["tests"].values())
        else "pass"
    )
    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    raise SystemExit(1 if core_failed else 0)


if __name__ == "__main__":
    main()
