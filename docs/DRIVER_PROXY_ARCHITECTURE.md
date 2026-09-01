# Flyt VM CUDA proxy architecture

## User-visible contract

The application, Python environment, Jupyter kernel, compiler, source tree, and
CPU work stay inside the KubeVirt VM.  The VM has no NVIDIA PCI function.  Flyt
forwards the public CUDA Runtime and Driver operations to a per-process executor
in the GPU Cell, which owns the whole physical GPU and joins CUDA MPS.

The supported path must not require an application-specific `LD_PRELOAD` and
must fail closed when an API is absent.  The initial compatibility profile is
CUDA 12.8, PyTorch 2.11, cuDNN 9, and `sm_120`.

## Evidence-driven boundary

An M1 experiment tried to use NVIDIA's unmodified `libcudart.so.12` with the
existing Cricket object as `libcuda.so.1`.  Importing PyTorch and the public
`cuInit`, `cuDeviceGetCount`, and `cuDeviceGet` RPCs succeeded.  Native cudart
then crashed while initializing the device.

The core dump and source inspection identified the boundary: native cudart uses
private driver export tables obtained through `cuGetExportTable`.  The current
client returns `CUDA_SUCCESS` with a null table.  Reconstructing private,
version-specific tables is not a stable compatibility contract.

Therefore Flyt keeps a compatible Runtime proxy and a public Driver proxy. The
same client image may implement both. Its initial exported ABI is:

- public `cuda*` Runtime entry points and CUDA registration hooks;
- public `cu*` Driver entry points;
- validated public vendor RPC entry points when a native library requires a
  private NVIDIA Driver ABI; cuBLAS is the first such hybrid path;
- Flyt's explicitly documented diagnostic entry points.

cuDNN, cuFFT, cuSOLVER, cuSPARSE, and NVML remain native until their public RPC
wrappers pass focused validation. Native cuBLAS cannot initialize through the
current proxy because it requires a private `cuGetExportTable`; exporting the
existing public Flyt cuBLAS RPC ABI avoids fabricating that private table while
keeping application launch free of `LD_PRELOAD`.

## Components

```text
KubeVirt VM
  application / Python / Jupyter
  NVIDIA cuDNN, cuFFT, Triton; hybrid Flyt cuBLAS ABI
  Flyt libcudart.so.12 + libcuda.so.1 public proxy
  Flyt guest session manager
          |
          | versioned public CUDA RPC
          v
GPU Cell Pod
  per-process executor and typed object table
  NVIDIA driver + CUDA MPS
  DRA whole-GPU claim
```

## Compatibility rules

1. Every exported symbol has a generated support status and conformance test.
2. Unsupported entry points return the documented CUDA not-supported error;
   they never call a null local symbol or return success with an invalid object.
3. Guest handles and device addresses are session-scoped tokens and are never
   accepted from another VM or process.
4. A session disconnect, VM stop, or heartbeat expiry reclaims its executor,
   CUDA context, SM allocation, and memory allocation.
5. Physical properties are virtualized to the Flyt allocation.
6. Only Flyt-labeled resources in the Flyt namespace may be mutated by test and
   recovery automation.

## Milestone gates

- M1: no application-specific preload; device query, vector add, and basic
  PyTorch tensor pass through the restricted public export surface.
- M2: no zombie executor or residual allocation after normal exit, SIGKILL, VM
  stop, or client-manager restart.
- M3-M5: typed objects, virtual address translation, streams/events/modules,
  VMM, memory pools, and Graph lifecycle pass their conformance probes.
- M6: the checked-in PyTorch matrix passes 19/19 using native or explicitly
  validated public vendor RPC ABIs, without application-specific preload.
- M7-M8: venv, Jupyter, extension builds, multiprocessing, two-VM concurrency,
  live allocation changes, and fault experiments pass.

Raw experiments and failures are retained under `results/`; a failure is not
converted into a pass by skipping a required feature.
