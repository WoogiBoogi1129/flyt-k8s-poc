#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
kubectl -n "$NAMESPACE" create configmap flyt-source-patches \
  --from-file="$ROOT_DIR/patches" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" create configmap flyt-probes \
  --from-file=cap_probe.cu="$ROOT_DIR/probes/cap_probe.cu" \
  --from-file=compute_probe.cu="$ROOT_DIR/probes/compute_probe.cu" \
  --from-file=memory_probe.cu="$ROOT_DIR/probes/memory_probe.cu" \
  --dry-run=client -o yaml | kubectl apply -f -
