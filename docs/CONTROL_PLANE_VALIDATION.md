# Stage 1 validation — 2026-09-16 UTC

Scope: CPU-only Kubernetes deployment and review reconciliation. No GPU runtime
readiness claim is made by these results.

- Source/image revision: `4ef0d5659d8c2372c2d3e0bbd7ac3386913af0c1`.
- Image: `ghcr.io/woogiboogi1129/flyt-control-plane:sha-4ef0d56`.
- Deployed immutable digest: `sha256:0e494a9094d9ec6fa0e904621e9a89082bd020894f630b1145b9ee2fbb2bc060`.
- Cluster: Kubernetes `v1.37.0`, CRI-O `1.37`, single CPU-only node `taeuk-node-0`.
- Existing infrastructure: KubeVirt `1.9.0`, HAMi `2.10.0`, NFS CSI `4.13.4`.
- Retained installation: namespace `flyt-stage1`, Helm release `stage1`, review mode.
- Both Deployments Ready, non-root containers, image pulled from GHCR using a
  namespace-local registry Secret. No credentials or private certificates are committed.

## Checks

| Check | Result |
|---|---|
| Python unit suite | 10 passed |
| Helm lint | Passed |
| Helm render default / acknowledged active / cert-manager configurations | Passed |
| Reject unacknowledged active mode / non-digest image | Passed |
| Chart/deploy CRD copies | Identical |
| Kubernetes server dry-run of CRDs | Passed |
| Review RBAC denies Pod create, VM update, Attachment create | Passed |
| CRD rejects invalid session count | Passed |
| Live TLS admission rejects unapproved Worker image | Passed |
| Valid CPU request reports GPUUnavailable and Ready=False | Passed |
| No allocation, finalizer, Worker creation or VM mutation | Passed |
| Repeated review does not churn status | Passed |
| Controller restart preserves review and observes later generation | Passed |
| Drain flag in review causes no drain execution | Passed |
| Channel deletion leaves no stuck finalizer | Passed |
| Second namespace/release installation | Passed (`flyt-stage1-portable` / `portable`) |
| Webhook scaled to zero | Request rejected by fail-closed admission |
| Webhook restored | Valid review request accepted (server dry-run) |
| Controller API access removed via scoped Role | Readiness became false; no process restart |
| Controller API access restored | Automatically recovered |
| TLS Secret server certificate rotated under same CA | New certificate served without Pod restart |

The fault tests were scoped to the temporary portable installation. Its Role
was restored and the release/namespace removed afterward. Shared CRDs and the
primary release remain. Integration test CRs, Halted VM and unmounted PV/PVC
were removed; no host SHM directory or GPU workload was created.

API 409/429/500/503 handling is unit-tested; a real apiserver outage or injected
server 5xx was not performed. The live authorization-outage test covered health
and retry recovery. Cert-manager was rendered only, not installed or exercised.
Kubernetes version acceptance in Chart.yaml is not a tested support matrix;
in particular the existing KubeVirt/Kubernetes combination needs separate
compatibility qualification before GPU PoC or production use.

## CI and evidence

The implementation commit passed GitHub Actions:

- [CPU unit checks, Helm lint and Docker image build](https://github.com/WoogiBoogi1129/flyt-k8s-poc/actions/runs/35124854484)
- [Static validation](https://github.com/WoogiBoogi1129/flyt-k8s-poc/actions/runs/35124854411)

Local detailed logs are under ignored `.local/`: `final-build.log`,
`final-push.log`, `final-install.log`, `final-review-smoke.json`,
`portable-install.log`, `operational.log`, `tls-check.txt`. These machine-local
artifacts are not portable evidence dependencies; the test entrypoint and
results above are committed for review.

Next phase requires a GPU node: real HAMi allocation, local SHM backing,
KubeVirt hook/Guest BAR mapping, supported CUDA execution, attachment evidence,
drain/detach and recovery. CPU validation does not cover these behaviors,
full PyTorch compatibility, HA, leader election, rolling availability or scale.
