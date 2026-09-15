#!/usr/bin/env python3
"""Isolate CUDA Graph capture failures by operation family."""

import argparse
import json

import torch


def run_pointwise():
    source = torch.randn(64, 64, device="cuda")
    output = torch.empty_like(source)
    capture_stream = torch.cuda.Stream()
    with torch.cuda.stream(capture_stream):
        torch.add(source, 1.0, out=output)
    capture_stream.synchronize()
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph, stream=capture_stream):
        torch.add(source, 1.0, out=output)
    graph.replay()
    torch.cuda.synchronize()
    return float(output.sum().cpu())


def run_matmul():
    left = torch.randn(64, 64, device="cuda")
    right = torch.randn(64, 64, device="cuda")
    output = torch.empty(64, 64, device="cuda")
    capture_stream = torch.cuda.Stream()
    with torch.cuda.stream(capture_stream):
        torch.mm(left, right, out=output)
    capture_stream.synchronize()
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph, stream=capture_stream):
        torch.mm(left, right, out=output)
    graph.replay()
    torch.cuda.synchronize()
    return float(output.sum().cpu())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("pointwise", "matmul"))
    args = parser.parse_args()
    checksum = run_pointwise() if args.operation == "pointwise" else run_matmul()
    print(json.dumps({"operation": args.operation, "checksum": checksum}))


if __name__ == "__main__":
    main()
