# Immutable runtime images

`images/flyt/Containerfile` builds Flyt once and emits three targets:

- `cluster-manager`: CUDA-free Flyt control plane;
- `gpu-cell`: CUDA/MPS node manager and Cricket server;
- `guest-artifacts`: files consumed by a separately versioned VM image build.

Runtime Pods do not install packages, clone source, or copy executables from a
shared build PVC. Build arguments must match `versions.lock.yaml`. Published
images should be referenced by digest from deployment overlays.
