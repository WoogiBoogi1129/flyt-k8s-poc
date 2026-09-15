#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need flock
need "$VIRTCTL"

lock_dir="$ROOT_DIR/.local/locks"
mkdir -p "$lock_dir"
exec 8>"$lock_dir/$VM_A.lock"
exec 9>"$lock_dir/$VM_B.lock"
flock -n 8 || die "another Flyt experiment is using $VM_A"
flock -n 9 || die "another Flyt experiment is using $VM_B"

result_dir="${RESULT_DIR:-$ROOT_DIR/results/$(date -u +%Y%m%dT%H%M%SZ)-multi-vm}"
mkdir -p "$result_dir"
seconds="${CONCURRENCY_SECONDS:-15}"
iterations="${CONCURRENCY_KERNEL_ITERATIONS:-200000}"
blocks="${CONCURRENCY_BLOCKS:-1024}"

flytctl() {
  kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
    /opt/flyt/bin/flytctl "$@"
}

ip_a="$(vmi_ip "$VM_A")"
ip_b="$(vmi_ip "$VM_B")"
command_a="/opt/flyt-client/run-with-flyt /opt/flyt-client/dynamic_compute_probe $seconds /tmp/flyt-concurrent-a.jsonl $iterations $blocks"
command_b="/opt/flyt-client/run-with-flyt /opt/flyt-client/dynamic_compute_probe $seconds /tmp/flyt-concurrent-b.jsonl $iterations $blocks"

set +e
virt_ssh "$VM_A" "$command_a" >"$result_dir/$VM_A.jsonl" 2>&1 &
pid_a=$!
virt_ssh "$VM_B" "$command_b" >"$result_dir/$VM_B.jsonl" 2>&1 &
pid_b=$!
set -e

sleep 5
flytctl list-vms | tee "$result_dir/list-vms-active.txt"
flytctl list-virt-servers | tee "$result_dir/list-virt-servers-active.txt"
kubectl -n "$NAMESPACE" exec flyt-gpu-cell -- nvidia-smi \
  --query-compute-apps=gpu_uuid,pid,process_name,used_memory \
  --format=csv,noheader | tee "$result_dir/gpu-processes-active.txt"

set +e
wait "$pid_a"; rc_a=$?
wait "$pid_b"; rc_b=$?
set -e
printf 'vm_a_rc=%d vm_b_rc=%d\n' "$rc_a" "$rc_b" | \
  tee "$result_dir/exit-codes.txt"

sleep 8
flytctl list-vms | tee "$result_dir/list-vms-after.txt"

[[ "$rc_a" -eq 0 && "$rc_b" -eq 0 ]]
grep -Fq "$ip_a" "$result_dir/list-vms-active.txt"
grep -Fq "$ip_b" "$result_dir/list-vms-active.txt"
for vm_name in "$VM_A" "$VM_B"; do
  test "$(grep -c '^{' "$result_dir/$vm_name.jsonl")" -gt 1
  ! grep -Eq '"finite":false|"finite": false' "$result_dir/$vm_name.jsonl"
done
! awk -F '[|]' '
  $2 ~ /^[[:space:]]*[0-9a-fA-F:.]+[[:space:]]*$/ &&
  $3 ~ /^[[:space:]]*[0-9]+[[:space:]]*$/ { found=1 }
  END { exit(found ? 0 : 1) }
' "$result_dir/list-vms-after.txt"

printf 'multi_vm_concurrency=PASS result_dir=%s\n' "$result_dir"
