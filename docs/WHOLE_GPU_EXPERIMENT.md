# Whole-GPU Flyt smoke experiment

This profile is the runnable fallback for a shared cluster that has NVIDIA DRA
whole GPUs but no image registry for the immutable runtime images. It builds
Flyt in a namespace-scoped builder Pod, stores artifacts in `flyt-build`, and
injects exactly one approved physical GPU into one GPU Cell.

## Safety boundary

- Set `GPU_UUID` to an explicitly approved, idle physical GPU.
- Set `GPU_MODE=whole`; the preflight rejects MIG-enabled or active GPUs.
- `deploy/templates/21-gpu-cell-whole-pvc.yaml` selects the exact UUID through
  `gpu.nvidia.com`; it never changes MIG mode.
- Start VM A first. VM B may remain halted until the single-VM smoke passes.
- All mutable resources stay in the labeled `flyt-system` namespace.

## Reproduction

Prepare ignored `config.env` and an experiment-only SSH key, then run:

```bash
make render
make validate
make preflight
make build-flyt
kubectl -n flyt-system wait --for=condition=Ready pod/flyt-builder --timeout=30m
make control-plane
make gpu-cell-whole
make vms
virtctl start -n flyt-system flyt-vm-a
./scripts/seed-vm-resources.sh
make install-basic-guest
TEST_VMS=flyt-vm-a make test
make evidence
```

Expected acceptance signals include:

```text
device_count=1
checksum_ok=true
reported_sm=<the VM's admitted Flyt SM quota>
last_error_name=Out of memory expected_oom=true
```

The GPU Cell reports the full physical capacity to Cluster Manager, while each
VM sees only its admitted `compute_units` and memory quota. A completed probe
disconnects and its virtual server is deallocated, so empty `list-vms` and
`list-virt-servers` output after the command is expected.

## Current validation

The profile has been validated with one KubeVirt VM, CUDA 12.8, an NVIDIA RTX
PRO 6000 Blackwell GPU, 46 admitted SMs, and an 8192 MiB Flyt memory quota.
PyTorch, VM B, dynamic quota changes, and multi-GPU/NCCL remain separate gates.

Both VM A and VM B have subsequently passed the bounded basic suite against
the same GPU Cell. An intentionally oversized concurrent compute stress is a
known failure: guest processes can segfault and leave stale virtual servers.
See `KNOWN_LIMITATIONS.md`; use the bounded probes for acceptance until crash
reconciliation is fixed.
