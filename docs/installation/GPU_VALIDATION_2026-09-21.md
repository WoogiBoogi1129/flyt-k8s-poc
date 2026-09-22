# gpu-4 infrastructure installation and FLYT validation

Status: infrastructure installation and basic tests COMPLETE; full FLYT Guest
path BLOCKED by missing QEMU ivshmem device. Final authoritative report:
[한국어 실행 보고서](INSTALLATION_REPORT_2026-09-22.md).
The sections below also retain intermediate troubleshooting history.

## Authorized scope

Keep Kubernetes 1.37; skip control-plane taint removal (already completed).
Install GPU Operator, HAMi, KubeVirt, validate basic GPU/VM behavior, then
prepare and exercise the repository's SHM control plane. Record failures and
fixes. Do not claim that CPU review tests validate GPU runtime behavior.

## Baseline

- Repository: feat/k8s-native-control-plane, c4b67fc812b01c076bdca5b5e2003fc2d66b5f0a.
- Kubernetes v1.37.0, Cilium 1.18.6; node Ready and CoreDNS Running.
- Node gpu-4; Ubuntu 24.04; four RTX PRO 6000 Blackwell Server Edition GPUs.
- Host driver 580.173.02, MIG disabled, /dev/kvm present.
- No GPU Operator, HAMi, KubeVirt Helm releases initially.
- Raw evidence directory (gitignored): .local/gpu-validation-20260921/.

## Issue 1: installed and running CRI-O versions differ

Evidence: dpkg reports cri-o 1.37.0-1.1, /usr/bin/crio reports 1.37.0,
but systemd launches /usr/local/bin/crio (1.35.0) via the locally installed
/usr/local/lib/systemd/system/crio.service. Node reports cri-o://1.35.0.

Fix prepared: fix-crio-service.sh creates a systemd drop-in selecting the
packaged /usr/bin/crio while preserving the existing environment options.
It backs up /etc/crio and records the effective unit, and rolls back the
drop-in if restart fails. User ran the script as root. Systemd now selects /usr/bin/crio and journal
confirms startup of 1.37.0. Node Ready, API readyz OK, runtime cri-o://1.37.0 verified after recovery.
The minor-version change triggers CRI-O sandbox cleanup/recreation; this is
a disruptive runtime operation on the single control-plane node. Kubernetes
objects and persistent volumes remain. Journal evidence: crio-upgrade.log.
No host workaround using privileged pods is used.

## Compatibility qualification

KubeVirt 1.9 lists Kubernetes 1.34–1.36 in its release support matrix.
Kubernetes 1.37 is an explicit experimental qualification in this task.
Basic VM operation, legacy Sidecar hook, shared storage, and real GPU execution
must be measured separately before declaring the overall experiment successful.

## Planned component ownership

- GPU Operator 26.7.0: Toolkit and monitoring; existing host driver retained.
- GPU Operator NVIDIA Device Plugin disabled; HAMi 2.10.0 owns GPU registration.
- MIG manager and sandbox GPU passthrough disabled.
- HAMi uses explicit nvidia RuntimeClass and CDI annotations in the final configuration.
- KubeVirt 1.9.0: VM lifecycle and the repository's legacy Sidecar hook.

## Results so far (KST)

- CPU control-plane unit suite: 10 tests passed.
- Live review integration: 8 checks passed, including TLS rejection, RBAC,
  stable reconciliation, controller restart and deletion without finalizers.
  Used deliberately absent node flyt-review-missing-node to test GPUUnavailable
  deterministically on a GPU cluster; this is a dependency-negative test.
  Evidence: review-smoke.json and review-smoke.log.
- Review controller/webhook deployed in flyt-review-validation.
- Active controller/webhook deployed and Ready in flyt-gpu-validation.
  Dedicated Retain local PV/PVC and Halted VM prepared.
  shm-channel-a reached BackingReady: real provision Pod completed, allocation
  backing files created and Sidecar PVC annotation inserted. VM still Halted.
- Worker: actual CUDA 12.8 CMake compile/link and image build passed.
- Guest library: image build passed; VM execution pending.
- Hook: image built with KubeVirt v1.9.0 sidecar-shim pinned to
  sha256:8eb25c016c09366ea4b2bbd524dfbb4872184a9aea67cc34c60ff78a11cb2661.
- CPU cross-process queue: 1000 successful request/response round trips,
  including wraparound of a 64-entry ring. This does not prove Guest BAR
  cache-coherency or GPU correctness. Harness: queue-smoke.c.
- GPU Operator Helm release deployed. NVIDIA CUDA validator passed real vector addition (50,000 elements).
  GFD and DCGM Ready; ClusterPolicy ready.
- HAMi Helm deployed; scheduler and device plugin both 2/2 Ready.
  Four physical GPUs are advertised as 40 logical shares (default 10 per GPU).
  Smoke Pod was allocated the requested GPU UUID, 1024 MiB and 25% SM quota.
  Actual CUDA result 42 and 1536 MiB allocation rejection under a 1024 MiB
  limit passed using CDI (hami-smoke-cdi.log).
- KubeVirt reports Deployed; virt-handler Ready and KVM/TUN/vhost-net
  device resources registered. Basic VM Running/Ready; SSH and cloud-init done.
- virtctl v1.9.0 downloaded and client version verified.
- NVIDIA CUDA, HAMi memory quota, and basic VM boot passed. FLYT Guest SHM
  remains blocked by QEMU lacking ivshmem-plain.

## Issue 2: private control-plane image cannot be pulled anonymously

The previously recorded GHCR digest returns unauthorized. No credentials were
requested or copied. Built current source locally using rootless Podman instead.
Control-plane, Worker and hook OCI archives are available in the evidence folder.
import-local-images.sh imports these archives into rootful containers/storage
(shared by this CRI-O installation) and creates two dedicated local SHM paths.
User executed image import successfully; SHM directories are 107:107 mode 0770.
The imported control-plane image started in flyt-review-validation.
OCI archive manifests have different digests from the original local build
manifest because export rewrites the manifest representation. Deployment uses
the archive digests in imported-image-digests.json, verified against the node
image inventory and actual Pod startup, rather than built-images.json.
No images have been pushed to an external registry.

## Issue 3: transient NVIDIA management CDI device resolution failure

Validator initially reports `unresolvable CDI devices management.nvidia.com/gpu=all`.
The error occurs before Toolkit finishes installation. Toolkit subsequently
created /var/run/cdi/management.nvidia.com-gpu.yaml and requested CRI-O restart.
Resolved after Toolkit finished configuring CDI and restarting CRI-O.
Runtime restart took roughly 90 seconds; node briefly became NotReady and image
inspection reported missing crio.sock. Node returned Ready and CUDA validator
completed successfully. Other image pulls resumed serially.
Evidence: nvidia-cuda-validator.log, runtime journal and Kubernetes Events.

## Download and test-harness corrections

virtctl's initial GitHub download returned HTTP 504; an alternate download query
and resumed transfer completed. Client version was verified as v1.9.0.
The new queue test harness initially shadowed a local rc variable and omitted
output_capacity; correcting the harness yielded 1000 successful round trips.
No production queue implementation change was required.

## Host actions completed by the user

Run as root, inspect the prepared scripts first:

```sh
bash /home/ubuntu/taeuk/flyt-k8s-poc-github/docs/installation/fix-crio-service.sh
bash /home/ubuntu/taeuk/flyt-k8s-poc-github/docs/installation/import-local-images.sh
```

Both host actions have now been executed by the user and verified. Do not rerun
the service script: it intentionally refuses an existing override.
The first changed the service binary with configuration backup; the second
imported local images and created /var/lib/flyt-poc-20260921/a and b (107:107).

## Issue 4: interrupted Helm operations retained pending-install

During CRI-O replacement, the API restarted. A transient forbidden response
occurred during recovery; cache initialization is a likely explanation, not
a separately proven root cause.
The two running Helm installs exited with a forbidden Services read, then their
release status remained pending-install. After API readiness returned,
`kubectl auth can-i get services -n kube-system` returned yes; no RBAC grant was
needed. Retrying Helm reported another operation in progress. Confirmed no
installer process remained, and no active SHM Channel or GPU client existed.
Uninstalled only the two incomplete releases (HAMi and the active validation
release), preserving the namespace, CRDs, TLS Secret, local PV/PVC and Halted VM.
Reinstallation uses the same source/configuration. The successful review release
was retained. HAMi's scheduler image now uses official registry.k8s.io and the
cluster's exact Kubernetes version; the original mirror was reachable, not
proven defective.

## Post-upgrade verification

CRI-O 1.37.0 and Kubernetes node Ready verified. NVIDIA CUDA validator passed
again on the new runtime (nvidia-cuda-validator-crio137.log). Toolkit's own Pod
restart requested one further CRI-O restart; this completed without another
minor-version transition. The active FLYT release was reinstalled successfully
with both controller and webhook Ready, and fail-closed admission registered.

## Upstream references

- [KubeVirt installation](https://kubevirt.io/user-guide/cluster_admin/installation/)
- [KubeVirt 1.9.0 release](https://github.com/kubevirt/kubevirt/releases/tag/v1.9.0)
- [NVIDIA GPU Operator installation](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html)
- HAMi chart: https://project-hami.github.io/HAMi/, pinned chart version 2.10.0;
  exact downloaded chart defaults are saved as hami-defaults.yaml in the evidence directory.

## Issue 5: management CDI cannot resolve HAMi's per-GPU UUID

The real FLYT Worker failed before process startup with:
`unresolvable CDI devices management.nvidia.com/gpu=GPU-772181af-05a4-0c24-de59-8487bd37c142`.
This is different from the earlier startup race for `management.nvidia.com/gpu=all`.
Toolkit's effective generated config selected mode=cdi and default-kind=
management.nvidia.com/gpu. Its management spec contains only the `all` device.
HAMi's envvar allocator correctly supplied NVIDIA_VISIBLE_DEVICES=<GPU UUID>.
Thus the runtime attempted a management CDI lookup that cannot resolve that UUID.

Attempted cdi.enabled=false and then explicit nvidia-legacy, but a separate
NVIDIA hook error occurred. Final fix: restore Operator CDI, configure HAMi
with deviceListStrategy=cdi-annotations, nvidiaDriverRoot=/ and nvidiaHookPath=
/usr/local/nvidia/toolkit/nvidia-ctk. HAMi generated its per-GPU CDI spec.
Real CUDA computation and memory quota smoke passed. See the final report
for the retained failed allocation and the new allocation's measured outcomes.
Evidence: worker-cdi-error.json and gpu-operator-legacy.log.

## Issue 6: fsGroup changes layout metadata permissions

The provisioner wrote layout.bin as 0640. Mounting the local PVC from Worker and
KubeVirt (fsGroup=107) changed it to 0660. The layout reader rejected any group
write bit and exited before opening the GPU session. A separate read-only PVC
inspection Pod confirmed owner/group 107:107 and mode 0660. The original library
reproduced the rejection in layout-before.log.

Changed runtime/shm/src/mapping.c to allow group-write only when BOTH file owner
and group match the process effective UID/GID. World-write, foreign group-write,
foreign owner with group-write, and symbolic links are still rejected. All six
regression tests pass in a rootless container, including foreign UID/GID tests.
This assumes the allocation UID/GID are trusted identities shared by its VM and
Worker. The new Worker image is pinned to
sha256:abe1dafb2c0361a68adab9426ec41b7ae31848872610ea649e98895946ffe2d5.
User imported it successfully; node inventory confirms the digest.

The original failed Channel a remains Draining: Worker detach was reported,
but the launcher was removed before a terminal Guest status was recorded.
Missing API objects are not used as evidence of detach. Backing files and
finalizer are retained; do not reuse this allocation or force cleanup.
A new allocation on the separate b PVC will be used for the corrected run.
