#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need "$VIRTCTL"

result_dir="${RESULT_DIR:-$ROOT_DIR/results/$(date -u +%Y%m%dT%H%M%SZ)-basic}"
mkdir -p "$result_dir"

kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
  /opt/flyt/bin/flytctl list-servernodes | tee "$result_dir/servernodes-before.txt"

for vm_name in "$VM_A" "$VM_B"; do
  {
    printf 'vm=%s ip=%s started=%s\n' \
      "$vm_name" "$(vmi_ip "$vm_name")" "$(date -Is)"
    virt_ssh "$vm_name" \
      '/opt/flyt-client/run-with-flyt /opt/flyt-client/cap_probe'
    virt_ssh "$vm_name" \
      '/opt/flyt-client/run-with-flyt /opt/flyt-client/compute_probe 200000 1024'
    virt_ssh "$vm_name" \
      '/opt/flyt-client/run-with-flyt /opt/flyt-client/memory_probe 256'
  } 2>&1 | tee "$result_dir/$vm_name.txt"
done

kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
  /opt/flyt/bin/flytctl list-vms | tee "$result_dir/vms-after.txt"
kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
  /opt/flyt/bin/flytctl list-virt-servers | tee "$result_dir/virt-servers-after.txt"
printf 'result_dir=%s\n' "$result_dir"
