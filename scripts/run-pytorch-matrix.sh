#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need jq
need python3
need comm
need flock
need "$VIRTCTL"

lock_dir="$ROOT_DIR/.local/locks"
mkdir -p "$lock_dir"
exec 8>"$lock_dir/$VM_A.lock"
exec 9>"$lock_dir/$VM_B.lock"
flock -n 8 || die "another Flyt experiment is using $VM_A"
flock -n 9 || die "another Flyt experiment is using $VM_B"

result_dir="${RESULT_DIR:-$ROOT_DIR/results/$(date -u +%Y%m%dT%H%M%SZ)-pytorch}"
mkdir -p "$result_dir"

flytctl() {
  kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
    /opt/flyt/bin/flytctl "$@"
}

has_active_clients() {
  flytctl list-vms | awk -F '[|]' '
    $2 ~ /^[[:space:]]*[0-9a-fA-F:.]+[[:space:]]*$/ &&
    $3 ~ /^[[:space:]]*[0-9]+[[:space:]]*$/ { found=1 }
    END { exit(found ? 0 : 1) }
  '
}

has_server_allocations() {
  flytctl list-servernodes | awk -F '[|]' '
    {
      memory=$5; compute=$7
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", memory)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", compute)
      if ((memory ~ /^[0-9]+$/ && memory != 0) ||
          (compute ~ /^[0-9]+$/ && compute != 0)) found=1
    }
    END { exit(found ? 0 : 1) }
  '
}

runtime_healthy() {
  [[ "$(kubectl -n "$NAMESPACE" get pod flyt-gpu-cell \
    -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null)" == "true" ]] && \
    flytctl list-servernodes | awk -F '[|]' -v expected="$EXPECTED_GPU_SM" '
      {
        gpu=$2; compute=$6
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", gpu)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", compute)
        if (gpu ~ /^[0-9]+$/ && compute == expected) found=1
      }
      END { exit(found ? 0 : 1) }
    '
}

gpu_cell_manifest() {
  printf '%s\n' "$RENDERED_DIR/20-gpu-cell.yaml"
}

reset_flyt_runtime() {
  local vm_name
  for vm_name in "$VM_A" "$VM_B"; do
    virt_ssh "$vm_name" \
      'sudo systemctl stop flyt-client-manager.service 2>/dev/null || true; \
       sudo ipcrm --all=msg 2>/dev/null || true; \
       sudo unlink /tmp/flyt-client-mgr 2>/dev/null || true' \
      >/dev/null 2>&1 || true
  done
  kubectl -n "$NAMESPACE" delete pod flyt-gpu-cell --ignore-not-found \
    --wait=true --timeout=3m
  kubectl -n "$NAMESPACE" rollout restart deployment/flyt-cluster-manager
  kubectl -n "$NAMESPACE" rollout status deployment/flyt-cluster-manager \
    --timeout=5m
  kubectl apply -f "$(gpu_cell_manifest)"
  kubectl -n "$NAMESPACE" wait --for=condition=Ready \
    pod/flyt-gpu-cell --timeout=10m
  for vm_name in "$VM_A" "$VM_B"; do
    virt_ssh "$vm_name" \
      'sudo systemctl start flyt-client-manager.service; \
       test "$(systemctl is-active flyt-client-manager.service)" = active'
  done
}

reset_guest_session() {
  local vm_name="$1"
  virt_ssh "$vm_name" '
    set -Eeuo pipefail
    sudo systemctl stop flyt-client-manager.service 2>/dev/null || true
    sudo ipcrm --all=msg 2>/dev/null || true
    sudo unlink /tmp/flyt-client-mgr 2>/dev/null || true
    sudo systemctl restart flyt-client-manager.service
    test "$(systemctl is-active flyt-client-manager.service)" = active
  '
}

compat_groups=(
  'environment:environment'
  'tensor-runtime:tensor_runtime'
  'autograd-optimizer:autograd_optimizer'
  'cublas:cublas'
  'amp:amp'
  'allocator:allocator'
  'serialization:serialization'
  'rng:rng'
  'fft:fft'
  'sparse:sparse'
  'linalg:linalg'
  'compile-modes:compile_modes'
  'allocator-async:allocator_cuda_malloc_async'
  'stream-event:stream_event'
  'cuda-graph:cuda_graph'
  'models:models'
  'cudnn:cudnn'
  'sdpa:sdpa'
  'pinned-memory:pinned_memory'
)

read -r -a compatibility_vms <<< "${COMPAT_VMS:-$VM_A $VM_B}"
(( ${#compatibility_vms[@]} > 0 )) || die 'COMPAT_VMS selected no virtual machines'
for vm_name in "${compatibility_vms[@]}"; do
  [[ "$vm_name" == "$VM_A" || "$vm_name" == "$VM_B" ]] || \
    die "COMPAT_VMS contains an unsupported virtual machine: $vm_name"
done

group_selected() {
  local group_name="$1"
  [[ -z "${COMPAT_GROUPS:-}" ]] || \
    [[ ",${COMPAT_GROUPS}," == *",${group_name},"* ]]
}

if [[ "${RESET_RUNTIME_FIRST:-false}" == "true" ]] || \
   ! runtime_healthy || has_active_clients || has_server_allocations; then
  reset_flyt_runtime
fi

compat_failures=0
if [[ "${RUN_COMPATIBILITY:-true}" == "true" ]]; then
  : > "$result_dir/compat-exit-codes.txt"
  for vm_name in "${compatibility_vms[@]}"; do
    for group_spec in "${compat_groups[@]}"; do
    group_name="${group_spec%%:*}"
    group_tests="${group_spec#*:}"
    group_selected "$group_name" || continue
    # A crashed CUDA process can leave guest IPC/manager state unusable even
    # after the server has removed its allocation.  Isolate every group so a
    # real failure cannot turn all subsequent groups into init failures.
    if has_active_clients || has_server_allocations || ! runtime_healthy; then
      reset_flyt_runtime
    else
      reset_guest_session "$vm_name"
    fi
    compat_rc=0
    set +e
    virt_ssh "$vm_name" \
      "timeout --signal=TERM --kill-after=15s 1200s \
         /opt/flyt-client/run-with-flyt \
         /opt/flyt-pytorch/venv/bin/python -u -X faulthandler \
         /opt/flyt-pytorch/tests/compat_matrix.py \
         --tests $group_tests \
         --output /opt/flyt-pytorch/$vm_name-$group_name-summary.json" \
      2>&1 | tee "$result_dir/$vm_name-$group_name.jsonl"
    compat_rc="${PIPESTATUS[0]}"
    set -e
    printf 'vm=%s group=%s exit_code=%s\n' \
      "$vm_name" "$group_name" "$compat_rc" | \
      tee -a "$result_dir/compat-exit-codes.txt"
    if (( compat_rc != 0 )) || \
       grep -q '"status": "fail"' "$result_dir/$vm_name-$group_name.jsonl"; then
      compat_failures=$((compat_failures + 1))
    fi
    sleep 2
      if ! runtime_healthy || has_active_clients || has_server_allocations; then
        printf 'reset_after vm=%s group=%s reason=unhealthy-or-stale-runtime\n' \
          "$vm_name" "$group_name" | \
          tee -a "$result_dir/compat-exit-codes.txt"
        reset_flyt_runtime
      else
        reset_guest_session "$vm_name"
      fi
    done
  done
else
  printf 'compatibility_matrix=skipped\n' > "$result_dir/compat-exit-codes.txt"
fi

# Dynamic reallocation is opt-in because it changes live Flyt quotas.
if [[ "${RUN_DYNAMIC:-false}" != "true" ]]; then
  printf '%s\n' 'dynamic_reallocation=skipped' | \
    tee "$result_dir/dynamic-status.txt"
  (( compat_failures == 0 )) || exit 1
  exit 0
fi
# Dynamic tests require a pristine client/virt-server mapping even if every
# compatibility process exited normally.
if ! runtime_healthy || has_active_clients; then
  reset_flyt_runtime
fi

# Flyt rejects a live memory change below the resource document's admission
# floor. Temporarily admit VM A at 4096 MiB for the 4/8 GiB transition and
# restore both VMs to 8192 MiB in the EXIT trap below.
restore_resource_documents() {
  MEMORY_A_MIB=8192 MEMORY_B_MIB=8192 \
    "$ROOT_DIR/scripts/seed-vm-resources.sh" >/dev/null 2>&1 || true
}
trap restore_resource_documents EXIT
MEMORY_A_MIB=4096 MEMORY_B_MIB=8192 \
  "$ROOT_DIR/scripts/seed-vm-resources.sh" \
  > "$result_dir/dynamic-required-resources-temporary.txt"

# Start a bounded native CUDA FMA workload in each guest. The separate PyTorch
# matrix above establishes framework compatibility; this persistent kernel isolates
# Flyt's live SM-reallocation behavior from PyTorch's cuBLAS handle lifecycle.
# systemd-run preserves logs even when the virtctl SSH connection closes.
for vm_name in "$VM_A" "$VM_B"; do
  virt_ssh "$vm_name" \
    'sudo systemctl stop flyt-dynamic-training.service >/dev/null 2>&1 || true; \
     sudo systemctl reset-failed flyt-dynamic-training.service >/dev/null 2>&1 || true; \
     sudo -u experiment truncate -s 0 /home/experiment/dynamic-training.jsonl; \
     sudo systemd-run --unit=flyt-dynamic-training --collect \
       --uid=experiment --gid=experiment --setenv=HOME=/home/experiment \
       --property=RuntimeMaxSec=420 \
       /opt/flyt-client/run-with-flyt \
       /opt/flyt-client/dynamic_compute_probe \
       300 /home/experiment/dynamic-training.jsonl 200000 1024'
done

wait_for_training_records() {
  local vm_name="$1" count=0
  for _ in $(seq 1 72); do
    count="$(virt_ssh "$vm_name" \
      'sudo sh -c '\''journalctl -u flyt-dynamic-training.service --no-pager --output=cat | grep -c "^{\\\"iteration\\\":" || true'\'' \
       2>/dev/null' | tr -cd '0-9')"
    if [[ "$count" =~ ^[0-9]+$ ]] && (( count >= 30 )); then
      return 0
    fi
    sleep 5
  done
  die "training records did not become ready for $vm_name (last count: $count)"
}

wait_for_training_records "$VM_A"
wait_for_training_records "$VM_B"
# Create a stable baseline window after both workloads are producing records.
sleep 45
kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
  /opt/flyt/bin/flytctl list-vms | tee "$result_dir/vms-running.txt"

client_for_ip() {
  local ip_address="$1"
  kubectl -n "$NAMESPACE" exec deploy/flyt-cluster-manager -- \
    /opt/flyt/bin/flytctl list-vms | \
    awk -F '[|]' -v ip="$ip_address" '
      {
        vm=$2; client=$3; active=$8
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", vm)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", client)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", active)
        if (vm == ip && client ~ /^[0-9]+$/ && active == "true") {
          print client
        }
      }' | tail -n 1
}

ip_a="$(vmi_ip "$VM_A")"
ip_b="$(vmi_ip "$VM_B")"
client_a="$(client_for_ip "$ip_a")"
client_b="$(client_for_ip "$ip_b")"
[[ "$client_a" =~ ^[0-9]+$ && "$client_b" =~ ^[0-9]+$ ]] || \
  die "could not identify Flyt client IDs for $ip_a and $ip_b"

restore_dynamic_clients() {
  set +e
  flytctl change-config --ip "$ip_a" --client-id "$client_a" \
    --sm-cores 46 >/dev/null 2>&1
  flytctl change-config --ip "$ip_a" --client-id "$client_a" \
    --memory 8192 >/dev/null 2>&1
  flytctl change-config --ip "$ip_b" --client-id "$client_b" \
    --sm-cores 46 >/dev/null 2>&1
  flytctl change-config --ip "$ip_b" --client-id "$client_b" \
    --memory 8192 >/dev/null 2>&1
  for vm_name in "$VM_A" "$VM_B"; do
    virt_ssh "$vm_name" \
      'sudo systemctl stop flyt-dynamic-training.service \
         flyt-quota-success.service flyt-quota-oom.service \
         >/dev/null 2>&1 || true' \
      >/dev/null 2>&1 || true
  done
  MEMORY_A_MIB=8192 MEMORY_B_MIB=8192 \
    "$ROOT_DIR/scripts/seed-vm-resources.sh" >/dev/null 2>&1 || true
}
trap restore_dynamic_clients EXIT

change_config() {
  local response
  response="$(flytctl change-config "$@" 2>&1)"
  printf '%s\n' "$response"
  grep -q '^200:' <<<"$response" || die "Flyt change-config failed"
}

{
  printf 'phase=initial vm_a=%s/%s vm_b=%s/%s time=%s epoch_s=%s\n' \
    "$ip_a" "$client_a" "$ip_b" "$client_b" "$(date -Is)" "$(date +%s)"
  change_config --ip "$ip_a" --client-id "$client_a" --sm-cores 92
  sleep 30
  printf 'phase=a92 time=%s epoch_s=%s\n' "$(date -Is)" "$(date +%s)"
  flytctl list-vms
  change_config --ip "$ip_a" --client-id "$client_a" --sm-cores 46
  change_config --ip "$ip_b" --client-id "$client_b" --sm-cores 92
  sleep 30
  printf 'phase=b92 time=%s epoch_s=%s\n' "$(date -Is)" "$(date +%s)"
  flytctl list-vms
  change_config --ip "$ip_b" --client-id "$client_b" --sm-cores 46
  change_config --ip "$ip_a" --client-id "$client_a" --memory 8192
  sleep 10
  printf 'phase=a-memory-8192 time=%s epoch_s=%s\n' \
    "$(date -Is)" "$(date +%s)"
  flytctl list-vms
  change_config --ip "$ip_a" --client-id "$client_a" --memory 4096
  sleep 10
  printf 'phase=a-memory-4096 time=%s epoch_s=%s\n' \
    "$(date -Is)" "$(date +%s)"
  flytctl list-vms
  change_config --ip "$ip_a" --client-id "$client_a" --memory 8192
} 2>&1 | tee "$result_dir/reallocation-control.txt"

# Collect and analyze the live-compute result before the independent PyTorch
# quota cases. A compatibility failure must not discard successful control
# plane and continuity evidence.
for vm_name in "$VM_A" "$VM_B"; do
  sleep 5
  virt_ssh "$vm_name" \
    'sudo journalctl -u flyt-dynamic-training --no-pager --output=cat' \
    > "$result_dir/$vm_name-dynamic-training.jsonl" 2>&1 || true
done

analysis_rc=0
python3 "$ROOT_DIR/pytorch/tests/analyze_reallocation.py" \
  --control "$result_dir/reallocation-control.txt" \
  --vm-a "$result_dir/$VM_A-dynamic-training.jsonl" \
  --vm-b "$result_dir/$VM_B-dynamic-training.jsonl" \
  --output "$result_dir/reallocation-analysis.json" || analysis_rc=$?

client_ids_for_ip() {
  local ip_address="$1"
  flytctl list-vms | awk -F '[|]' -v ip="$ip_address" '
    {
      vm=$2; client=$3
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", vm)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", client)
      if (vm == ip && client ~ /^[0-9]+$/) print client
    }' | sort -u
}

run_quota_case() {
  local name="$1" mib="$2" expectation="$3" output="$4"
  local before after new_client='' unit_state=''
  before="$(client_ids_for_ip "$ip_a")"
  if ! virt_ssh "$VM_A" \
    "sudo systemctl stop $name.service >/dev/null 2>&1 || true; \
     sudo systemctl reset-failed $name.service >/dev/null 2>&1 || true; \
     sudo systemd-run --unit=$name --collect \
       --uid=experiment --gid=experiment --setenv=HOME=/home/experiment \
       /opt/flyt-client/run-with-flyt \
       /opt/flyt-pytorch/venv/bin/python \
       /opt/flyt-pytorch/tests/quota_allocation.py \
       --delay-seconds 25 --mib $mib --expect $expectation"; then
    printf 'quota_case=%s error=failed-to-start\n' "$name" >&2
    return 1
  fi

  for _ in $(seq 1 20); do
    sleep 1
    after="$(client_ids_for_ip "$ip_a")"
    new_client="$(comm -13 \
      <(printf '%s\n' "$before" | sed '/^$/d' | sort -u) \
      <(printf '%s\n' "$after" | sed '/^$/d' | sort -u) | tail -n 1)"
    [[ "$new_client" =~ ^[0-9]+$ ]] && break
  done
  if ! [[ "$new_client" =~ ^[0-9]+$ ]]; then
    printf 'quota_case=%s error=client-not-found\n' "$name" >&2
    return 1
  fi

  local quota_response
  quota_response="$(flytctl change-config --ip "$ip_a" \
    --client-id "$new_client" --memory 4096 2>&1)" || true
  printf '%s\n' "$quota_response"
  if ! grep -q '^200:' <<<"$quota_response"; then
    printf 'quota_case=%s error=quota-update-failed\n' "$name" >&2
    return 1
  fi
  for _ in $(seq 1 48); do
    unit_state="$(virt_ssh "$VM_A" \
      "sudo systemctl is-active $name.service 2>/dev/null || true" | \
      tail -n 1 | tr -d '\r')"
    [[ "$unit_state" != "active" && "$unit_state" != "activating" ]] && break
    sleep 5
  done
  if [[ "$unit_state" == "active" || "$unit_state" == "activating" ]]; then
    printf 'quota_case=%s error=timeout\n' "$name" >&2
    return 1
  fi
  if ! virt_ssh "$VM_A" \
    "sudo journalctl -u $name --no-pager --output=cat" \
    2>&1 | tee "$output"; then
    return 1
  fi
  if ! grep -q "\"result\": \"$expectation\"" "$output"; then
    printf 'quota_case=%s error=unexpected-result expected=%s\n' \
      "$name" "$expectation" >&2
    return 1
  fi
}

quota_failures=0
if [[ "${RUN_QUOTA_CASES:-true}" == "true" ]]; then
  run_quota_case flyt-quota-success 3072 success \
    "$result_dir/vm-a-memory-within-quota.jsonl" || \
    quota_failures=$((quota_failures + 1))
  run_quota_case flyt-quota-oom 5120 oom \
    "$result_dir/vm-a-memory-over-quota.jsonl" || \
    quota_failures=$((quota_failures + 1))
else
  printf 'quota_cases=skipped\n' > "$result_dir/quota-cases.txt"
fi

printf 'result_dir=%s\n' "$result_dir"
printf 'compat_failed_groups=%s reallocation_analysis_exit_code=%s quota_failures=%s\n' \
  "$compat_failures" "$analysis_rc" "$quota_failures"
(( compat_failures == 0 && analysis_rc == 0 && quota_failures == 0 ))
