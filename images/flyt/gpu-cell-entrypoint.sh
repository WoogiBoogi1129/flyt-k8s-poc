#!/usr/bin/env bash
set -Eeuo pipefail

: "${FLYT_NODE_MANAGER_CONFIG:?FLYT_NODE_MANAGER_CONFIG is required}"
: "${FLYT_ALLOWED_GPU_UUIDS:?FLYT_ALLOWED_GPU_UUIDS is required}"

mps_pipe="${CUDA_MPS_PIPE_DIRECTORY:-/run/flyt-mps/pipe}"
mps_log="${CUDA_MPS_LOG_DIRECTORY:-/var/log/flyt-mps}"

cleanup() {
  set +e
  [[ -n "${node_manager_pid:-}" ]] && kill -TERM "$node_manager_pid" 2>/dev/null
  [[ -n "${node_manager_pid:-}" ]] && wait "$node_manager_pid" 2>/dev/null
  pkill -TERM -P 1 cricket-rpc-server 2>/dev/null
  printf 'quit\n' | CUDA_MPS_PIPE_DIRECTORY="$mps_pipe" \
    nvidia-cuda-mps-control 2>/dev/null
  [[ -n "${rpcbind_pid:-}" ]] && kill -TERM "$rpcbind_pid" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 0' INT TERM

inventory="$(nvidia-smi -L)"
printf '%s\n' "$inventory"
mapfile -t visible_uuids < <(nvidia-smi --query-gpu=uuid --format=csv,noheader | sed '/^[[:space:]]*$/d')
[[ ${#visible_uuids[@]} -eq 1 ]] || {
  echo "expected exactly one DRA-injected GPU, observed ${#visible_uuids[@]}" >&2
  exit 1
}
read -r -a allowed_gpu_uuids <<< "$FLYT_ALLOWED_GPU_UUIDS"
allowed=false
for uuid in "${allowed_gpu_uuids[@]}"; do
  [[ $uuid =~ ^GPU-[A-Fa-f0-9-]+$ ]] || {
    echo "invalid UUID in FLYT_ALLOWED_GPU_UUIDS" >&2
    exit 1
  }
  [[ "${visible_uuids[0]}" == "$uuid" ]] && allowed=true
done
[[ $allowed == true ]] || {
  echo "observed GPU ${visible_uuids[0]} is not in the approved allow-list" >&2
  exit 1
}

printf '%s\n' "${visible_uuids[0]}" > /run/flyt-mps/gpu.uuid

install -d "$mps_pipe" "$mps_log" /run/rpcbind
rpcbind -f -w &
rpcbind_pid=$!
for _ in $(seq 1 20); do
  rpcinfo -p 127.0.0.1 >/dev/null 2>&1 && break
  kill -0 "$rpcbind_pid"
  sleep 0.25
done
rpcinfo -p 127.0.0.1 >/dev/null

export CUDA_VISIBLE_DEVICES=0
export CUDA_MPS_PIPE_DIRECTORY="$mps_pipe"
export CUDA_MPS_LOG_DIRECTORY="$mps_log"
export CUDA_MPS_ENABLE_PER_CTX_DEVICE_MULTIPROCESSOR_PARTITIONING=1
export LD_LIBRARY_PATH="/opt/flyt/bin:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
nvidia-cuda-mps-control -d

/opt/flyt/bin/flyt-node-manager &
node_manager_pid=$!
wait "$node_manager_pid"
