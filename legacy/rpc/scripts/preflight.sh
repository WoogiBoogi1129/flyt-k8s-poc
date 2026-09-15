#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need jq
need nvidia-smi
need_cluster_config
blocked=0

printf 'timestamp=%s\n' "$(date -Is)"
printf 'namespace=%s node=%s target_gpu=%s\n' \
  "$NAMESPACE" "$NODE_NAME" "$GPU_UUID"
kubectl version -o yaml | sed -n '1,35p'
kubectl get node "$NODE_NAME" -o wide
kubectl get kubevirt -A -o \
  custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="Available")].status'

gpu_line="$(nvidia-smi -i "$GPU_UUID" \
  --query-gpu=index,uuid,name,mig.mode.current,memory.used,memory.total \
  --format=csv,noheader,nounits)"
printf 'gpu=%s\n' "$gpu_line"
mig_mode="$(awk -F', *' '{print $4}' <<<"$gpu_line")"
if [[ "$mig_mode" != "Disabled" ]]; then
  printf 'BLOCKED: target GPU MIG mode is %s; whole-GPU deployment requires Disabled.\n' \
    "$mig_mode" >&2
  blocked=1
fi

printf '%s\n' '--- DRA claims in all namespaces ---'
kubectl get resourceclaims -A -o wide
printf '%s\n' '--- allocated DRA devices and owners ---'
kubectl get resourceclaims -A -o json | jq -r '
  .items[]
  | .metadata.namespace as $namespace
  | .metadata.name as $claim
  | (.status.reservedFor // []) as $owners
  | .status.allocation.devices.results[]?
  | [$namespace, $claim, .pool, .device,
     ($owners | map(.resource + "/" + .name) | join(","))]
  | @tsv'
printf '%s\n' '--- target GPU ResourceSlices ---'
kubectl get resourceslices -o json | jq -r --arg uuid "$GPU_UUID" '
  .items[]
  | .metadata.name as $slice
  | .spec.devices[]?
  | select((.attributes.uuid.string // "") == $uuid
        or (.attributes.parentUUID.string // "") == $uuid)
  | [$slice, .name, (.attributes.type.string // ""),
     (.attributes.profile.string // "whole"),
     (.attributes.uuid.string // ""),
     (.attributes.parentUUID.string // "")]
  | @tsv' || true

# Treat whole-GPU mode as a hard deployment gate. Inventory above identifies
# conflicting owners without mutating them.
if (( blocked )); then
  printf '%s\n' \
    'GPU_GATE=BLOCKED (non-GPU build/control-plane validation may continue)' >&2
  exit 2
fi

gpu_processes="$(nvidia-smi -i "$GPU_UUID" \
  --query-compute-apps=pid,process_name,used_memory \
  --format=csv,noheader,nounits 2>/dev/null || true)"
if [[ -n "$gpu_processes" ]]; then
  gpu_cell_ready="$(kubectl -n "$NAMESPACE" get pod flyt-gpu-cell \
    -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || true)"
  unexpected_processes="$(awk -F', *' \
    '$2 != "nvidia-cuda-mps-server" {print}' <<<"$gpu_processes")"
  if [[ "${ALLOW_ACTIVE_FLYT:-false}" == "true" && \
        "$gpu_cell_ready" == "true" && -z "$unexpected_processes" ]]; then
    printf 'POST_DEPLOY: accepted active Flyt GPU Cell MPS process:\n%s\n' \
      "$gpu_processes"
  else
    printf 'BLOCKED: target GPU has active compute processes:\n%s\n' \
      "$gpu_processes" >&2
    exit 2
  fi
fi

printf '%s\n' 'GPU_GATE=PASS'
