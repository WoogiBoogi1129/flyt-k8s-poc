#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

vm_name="${TEST_VM:-$VM_A}"
result_dir="${RESULT_DIR:-$ROOT_DIR/results/$(date -u +%Y%m%dT%H%M%SZ)-driver-proxy}"
mkdir -p "$result_dir"
need flock
mkdir -p "$ROOT_DIR/.local/locks"
exec 8>"$ROOT_DIR/.local/locks/$vm_name.lock"
flock -n 8 || die "another Flyt experiment is using $vm_name"

set +e
virt_ssh "$vm_name" '
  timeout --signal=TERM --kill-after=5s 90s \
    env -u LD_PRELOAD \
      SM_CORE="${SM_CORE:-46}" \
      PYTORCH_CUDA_ALLOC_CONF=backend:native \
      LD_LIBRARY_PATH=/opt/flyt-client:/opt/flyt-pytorch/cuda/lib64 \
      /opt/flyt-pytorch/venv/bin/python -u -X faulthandler - <<"PY"
import json
import torch

result = {
    "torch": torch.__version__,
    "available": torch.cuda.is_available(),
    "count": torch.cuda.device_count(),
}
if result["available"]:
    result["name"] = torch.cuda.get_device_name(0)
    result["tensor"] = torch.arange(8, device="cuda").cpu().tolist()
print(json.dumps(result, sort_keys=True))
PY
' 2>&1 | tee "$result_dir/$vm_name.log"
rc="${PIPESTATUS[0]}"
set -e
printf 'vm=%s exit_code=%s\n' "$vm_name" "$rc" | tee "$result_dir/status.txt"
exit "$rc"
