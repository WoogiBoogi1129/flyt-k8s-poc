#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need sha256sum
need nvidia-smi
need_cluster_config

evidence_dir="${EVIDENCE_DIR:-$ROOT_DIR/evidence/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$evidence_dir"

kubectl version -o yaml > "$evidence_dir/kubernetes-version.yaml"
kubectl get node "$NODE_NAME" -o yaml > "$evidence_dir/node.yaml"
kubectl -n "$NAMESPACE" get pods,deploy,svc,pvc,vm,vmi,resourceclaims \
  -o wide > "$evidence_dir/workloads.txt"
kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp \
  > "$evidence_dir/events.txt"
kubectl get resourceclaims -A -o wide > "$evidence_dir/dra-claims-all.txt"
nvidia-smi -i "$GPU_UUID" -q > "$evidence_dir/target-gpu.txt"
nvidia-smi -i "$GPU_UUID" \
  --query-gpu=index,uuid,mig.mode.current,mig.mode.pending \
  --format=csv,noheader > "$evidence_dir/target-gpu-mig-mode.csv"
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory \
  --format=csv > "$evidence_dir/gpu-processes.csv" || true

kubectl -n "$NAMESPACE" logs pod/flyt-builder \
  > "$evidence_dir/builder.log" 2>&1 || true
kubectl -n "$NAMESPACE" logs pod/flyt-pytorch-builder \
  > "$evidence_dir/pytorch-builder.log" 2>&1 || true
kubectl -n "$NAMESPACE" logs deploy/flyt-mongodb \
  > "$evidence_dir/mongodb.log" 2>&1 || true
kubectl -n "$NAMESPACE" logs deploy/flyt-cluster-manager \
  > "$evidence_dir/cluster-manager.log" 2>&1 || true
kubectl -n "$NAMESPACE" logs pod/flyt-gpu-cell \
  > "$evidence_dir/gpu-cell.log" 2>&1 || true

for command_name in list-vms list-servernodes list-virt-servers; do
  kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
    /opt/flyt/bin/flytctl "$command_name" \
    > "$evidence_dir/flytctl-$command_name.txt" 2>&1 || true
done

kubectl -n "$NAMESPACE" exec pod/flyt-builder -- /bin/bash -lc '
  set -e
  cat /workspace/out/FLYT_COMMIT
  sha256sum -c /workspace/out/ARTIFACTS.sha256
  sha256sum -c /workspace/flyt-guest-bundle.tar.gz.sha256
' > "$evidence_dir/flyt-build-acceptance.txt" 2>&1 || true

kubectl -n "$NAMESPACE" exec pod/flyt-pytorch-builder -- /bin/bash -lc '
  set -e
  cat /workspace/wheels/PYTORCH_COMMIT
  cd /workspace/wheels
  sha256sum -c SHA256SUMS
  test -s /workspace/wheels/SM120_BUILD_FLAGS
  test -s /workspace/wheels/SHARED_CUDART_BUILD_FLAGS
  test -s /workspace/wheels/DYNAMIC_CUDART_NEEDED
' > "$evidence_dir/pytorch-build-acceptance.txt" 2>&1 || true

preflight_status=0
ALLOW_ACTIVE_FLYT=true "$ROOT_DIR/scripts/preflight.sh" \
  > "$evidence_dir/preflight.txt" 2>&1 || \
  preflight_status=$?
printf 'exit_code=%s\n' "$preflight_status" \
  > "$evidence_dir/preflight-exit-code.txt"

# Export only non-secret VM resource documents. MongoDB credentials never
# leave the container environment.
kubectl -n "$NAMESPACE" exec deploy/flyt-mongodb -- /bin/bash -lc '
  mongosh --quiet \
    --username "$MONGO_INITDB_ROOT_USERNAME" \
    --password "$MONGO_INITDB_ROOT_PASSWORD" \
    --authenticationDatabase admin \
    --eval '\''printjson(db.getSiblingDB("flyt").vm_required_resources.find({}, {_id: 0}).toArray())'\''
' > "$evidence_dir/vm-required-resources.json" 2>&1 || true

cp "$ROOT_DIR/versions.lock.yaml" "$evidence_dir/versions.lock.yaml"
find "$ROOT_DIR/patches" -maxdepth 1 -type f -name '*.patch' -print0 | \
  sort -z | xargs -0 sha256sum > "$evidence_dir/source-patches.sha256"

latest_api_audit="$(find "$ROOT_DIR/results" -maxdepth 1 -type d \
  -name '*-api-audit' -print 2>/dev/null | sort | tail -n 1)"
if [[ -n "$latest_api_audit" ]]; then
  mkdir -p "$evidence_dir/api-audit"
  cp -a "$latest_api_audit"/. "$evidence_dir/api-audit/"
fi

# Preserve the latest measured result from each acceptance layer. These are
# copied after the live commands so the evidence bundle contains both the
# final cluster state and the workload-level observations that produced the
# verdict.
for result_pattern in '*-basic' '*-pytorch-final' '*-pytorch-linalg-rerun' \
  '*-dynamic-fma-final*'; do
  latest_result="$(find "$ROOT_DIR/results" -maxdepth 1 -type d \
    -name "$result_pattern" -print 2>/dev/null | sort | tail -n 1)"
  if [[ -n "$latest_result" ]]; then
    result_name="$(basename "$latest_result")"
    mkdir -p "$evidence_dir/results/$result_name"
    cp -a "$latest_result"/. "$evidence_dir/results/$result_name/"
  fi
done

{
  find "$ROOT_DIR/scripts" "$ROOT_DIR/pytorch" -type f -name '*.sh' \
    -print0 | xargs -0 -n 1 bash -n
  python3 -c 'import ast, pathlib, sys; root = pathlib.Path(sys.argv[1]); [ast.parse(p.read_text()) for p in (root / "pytorch/tests").glob("*.py")]' \
    "$ROOT_DIR"
  for manifest in "$RENDERED_DIR"/*.yaml; do
    kubectl apply --dry-run=client -f "$manifest" >/dev/null
  done
  git -C "$ROOT_DIR" diff --check -- . ':(exclude)patches/*.patch'
  printf '%s\n' 'shell=PASS python_ast=PASS kubernetes_client_dry_run=PASS git_diff_check=PASS'
} > "$evidence_dir/static-validation.txt" 2>&1

find "$evidence_dir" -type f ! -name SHA256SUMS -print0 | \
  sort -z | xargs -0 sha256sum > "$evidence_dir/SHA256SUMS"
printf 'evidence_dir=%s\n' "$evidence_dir"
