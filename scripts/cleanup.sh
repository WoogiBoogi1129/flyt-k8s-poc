#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl

# The default rollback is deliberately non-destructive: stop only these two
# VM definitions and remove the Flyt GPU consumer. Shared Cricket templates,
# other namespaces, MIG configuration, build artifacts, and PVCs are retained.
for vm_name in "$VM_A" "$VM_B"; do
  if kubectl -n "$NAMESPACE" get vm "$vm_name" >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" patch vm "$vm_name" --type=merge \
      -p '{"spec":{"runStrategy":"Halted"}}'
  fi
done
kubectl -n "$NAMESPACE" delete pod flyt-gpu-cell \
  --ignore-not-found --wait=true

if [[ "${1:-}" == "--purge" ]]; then
  [[ "${CONFIRM_PURGE:-}" == "$NAMESPACE/flyt-poc" ]] || \
    die "set CONFIRM_PURGE=$NAMESPACE/flyt-poc to delete PoC objects and PVCs"
  kubectl -n "$NAMESPACE" delete vm "$VM_A" "$VM_B" --ignore-not-found
  kubectl -n "$NAMESPACE" delete deploy flyt-cluster-manager flyt-mongodb \
    --ignore-not-found
  kubectl -n "$NAMESPACE" delete svc flyt-cluster-manager flyt-mongodb \
    --ignore-not-found
  kubectl -n "$NAMESPACE" delete pod flyt-builder --ignore-not-found
  kubectl -n "$NAMESPACE" delete pod flyt-pytorch-builder \
    --ignore-not-found
  kubectl -n "$NAMESPACE" delete networkpolicy \
    flyt-control-plane-ingress flyt-gpu-cell-ingress --ignore-not-found
  kubectl -n "$NAMESPACE" delete configmap \
    flyt-config flyt-source-patches flyt-guest-input \
    flyt-gpu-cell-input \
    flyt-pytorch-build-input \
    --ignore-not-found
  kubectl -n "$NAMESPACE" delete secret flyt-mongodb-credentials \
    --ignore-not-found
  kubectl -n "$NAMESPACE" delete pvc flyt-build --ignore-not-found
  kubectl -n "$NAMESPACE" delete pvc flyt-pytorch-build \
    --ignore-not-found
fi
