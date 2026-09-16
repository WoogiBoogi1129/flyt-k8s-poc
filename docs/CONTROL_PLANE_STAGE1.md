# CPU Kubernetes control plane — Stage 1

Stage 1 packages the SHM control plane as Kubernetes resources. The default
`review` mode validates references and records status/Events without allocating
SHM, adding finalizers, starting a VM, or creating a GPU Worker. On CPU-only
nodes a valid request reports `GPUUnavailable`, never successful GPU readiness.
The CUDA runtime, BAR mapping, HAMi allocation, drain/detach and failure recovery
still require a GPU-node PoC. This is not a production or HA release.

## Build and publish

```sh
docker build -f images/flyt/ControlPlane.Containerfile -t ghcr.io/woogiboogi1129/flyt-control-plane:YOUR_REVISION .
docker push ghcr.io/woogiboogi1129/flyt-control-plane:YOUR_REVISION
```

Without Docker, install the official `crane` binary (validated with v0.22.1),
then run `python3 scripts/build-control-plane.py` and
`crane push .local/control-plane.tar ghcr.io/woogiboogi1129/flyt-control-plane:YOUR_REVISION`.
The daemonless helper currently targets linux/amd64. Deploy the returned digest.
The image contains Python stdlib only; no CUDA, Torch, host kubeconfig or credentials.
CI runs CPU unit checks, chart lint and Docker build; it does not auto-publish images.

## Install in a dedicated namespace

Prerequisites: Kubernetes API/RBAC/admission access, Helm 3, Python 3, OpenSSL,
registry pull access and KubeVirt CRDs for full review tests. The chart accepts
Kubernetes >=1.29; only the version in the validation report has been exercised.
KubeVirt/HAMi/NFS are independently installed infrastructure, not chart dependencies.
NFS is not a substitute for the node-local SHM backing required by the GPU runtime.

```sh
kubectl create namespace flyt-stage1
python3 scripts/create-review-tls.py --namespace flyt-stage1 --release stage1 --output .local/tls-stage1
kubectl -n flyt-stage1 create secret tls flyt-admission-tls --cert=.local/tls-stage1/tls.crt --key=.local/tls-stage1/tls.key
python3 scripts/install-control-plane.py --namespace flyt-stage1 --release stage1 \
  --image-digest sha256:YOUR_64_HEX_DIGEST --ca .local/tls-stage1/ca.crt \
  --values deploy/examples/values-cpu-review.yaml
```

For a private GHCR package, provision an `imagePullSecret` in this namespace and
pass a values file containing `imagePullSecrets: [{name: flyt-ghcr}]`. Keep registry
credentials, TLS keys and rendered local configuration out of Git. `.local/` is ignored.
The CPU example tolerates a control-plane taint; omit it on ordinary workers.
Use one release per dedicated namespace. Release names should be short enough
for generated Service names and certificate DNS names (the helper truncates at 48).

The installer checks chart inputs and CRD ownership, explicitly upgrades CRDs,
waits for controller/webhook readiness, then registers fail-closed admission.
Existing RPC or unlabelled CRDs are rejected; use a separate cluster or plan a
schema migration. Helm itself does not upgrade/delete files under `crds/`:
https://helm.sh/docs/chart_best_practices/custom_resource_definitions/ .
Upgrades keep admission registered. Single replicas/Recreate imply temporary
admission unavailability during upgrades; requests fail closed and should retry.

The lab CA expires after 365 days and server certificate after 90 days. Rotate
Secret certificate/key before expiry; the webhook reloads them without restart.
When changing CA, update the webhook caBundle too, with an overlap trust bundle.
For cert-manager, the chart supports `tls.certManager.enabled=true` and
`tls.certManager.issuerRef`; apply CRDs first, install with
`webhook.registrationEnabled=false`, wait for Certificate and Pods, then enable
registration through Helm. This optional path is rendered but not live-tested;
the install helper above deliberately handles external Secrets only.
See https://cert-manager.io/docs/concepts/ca-injector/ .

## Behavior and operation

Controller and webhook have separate Deployments, ServiceAccounts and RBAC.
Review RBAC cannot create Pods/Attachments or update VMs. Containers run non-root
with read-only root filesystems. Namespace, timeouts, retry interval, scheduling,
resources and log level are values rather than host-specific constants.

The controller periodically lists Channels (not a watch/leader-election operator).
Status uses resourceVersion preconditions. API conflicts, 429 and 5xx retry on
subsequent reconciliation without marking a Channel permanently Failed or
performing cleanup. Missing resources remain pending; semantic errors are
reported. Conditions include observedGeneration and stable transition timestamps.
Unchanged review results do not rewrite status or emit duplicate Events.

Both processes expose `/livez`, `/readyz`, `/metrics` on port 8080 inside the Pod.
Readiness expires after API checks become stale; liveness stays independent of
API availability. Logs are JSON on stdout. Metrics are minimal process/reconcile
metrics, without a bundled monitoring stack. Reconcile is idempotent in review
mode and resumes after restart. TLS webhook errors deny requests in its namespace.

```sh
python3 -m unittest discover -s tests/control -v
python3 tests/integration/review_smoke.py --namespace flyt-stage1 --release stage1 \
  --node YOUR_CPU_NODE --report .local/review-smoke.json
kubectl -n flyt-stage1 get deploy,pods
kubectl -n flyt-stage1 logs deploy/stage1-flyt-controller
helm uninstall stage1 -n flyt-stage1
```

The integration test creates and removes its own CRs, Halted VM and unmounted
local PV/PVC; use a dedicated CPU review installation. Uninstall preserves CRDs,
custom resources, Secrets and namespace. Inspect and remove those separately
only after checking other releases and any active allocations.

## GPU handoff

`deploy/examples/values-gpu-poc.yaml` explicitly opts into experimental active
mode. Do not switch a populated review namespace in place: use a separate PoC
namespace and independently review the wider RBAC and admission rules.
Prepare pinned Worker/hook images, approved GPUProfile, exact UID references,
node-local PVC and compatible KubeVirt/HAMi/GPU drivers. Validate allocation,
VM/hook/PVC sharing, BAR mapping, worker execution, detach/drain, restart and
failure recovery before making readiness claims. Active runtime behavior is
largely inherited and has not been certified by CPU tests.

`scripts/render-shm.py` is retained as a historical experimental layout; use the
Helm chart and installer for Stage 1. It is not the supported review deployment.
