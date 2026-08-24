#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need comm

result_dir="${RESULT_DIR:-$ROOT_DIR/results/$(date -u +%Y%m%dT%H%M%SZ)-api-audit}"
mkdir -p "$result_dir"
flyt_source="${FLYT_SOURCE_DIR:-$ROOT_DIR/.cache/flyt-source}"

grep -nHi 'not implemented' "$flyt_source"/cpu/cpu-client-*.c \
  > "$result_dir/flyt-explicit-client-stubs.txt" || true

kubectl -n "$NAMESPACE" exec pod/flyt-builder -- bash -lc '
  nm -D --defined-only /workspace/out/guest/cricket-client.so |
    awk "NF >= 3 {print \$3}" | sed "s/@.*//" | sort -u
' > "$result_dir/flyt-exported-symbols.txt"

kubectl -n "$NAMESPACE" exec pod/flyt-pytorch-builder -- bash -lc '
  find /workspace/src/pytorch/build -type f -name "*.so" -print0 |
    xargs -0 -r nm -D --undefined-only 2>/dev/null |
    awk "{print \$NF}" | sed "s/@.*//" |
    grep -E "^(cuda|cu[A-Z]|cublas|cudnn|cusolver|cusparse|cufft|curand|nvrtc|nvml)" |
    sort -u
' > "$result_dir/pytorch-cuda-undefined-symbols.txt"

comm -12 \
  "$result_dir/flyt-exported-symbols.txt" \
  "$result_dir/pytorch-cuda-undefined-symbols.txt" \
  > "$result_dir/directly-interposed-symbols.txt"
comm -23 \
  "$result_dir/pytorch-cuda-undefined-symbols.txt" \
  "$result_dir/flyt-exported-symbols.txt" \
  > "$result_dir/not-directly-interposed-symbols.txt"

families=(
  'cuda_runtime|^cuda'
  'cuda_driver|^cu[A-Z]'
  'cublas|^cublas([^L]|L[^t])'
  'cublasLt|^cublasLt'
  'cudnn|^cudnn'
  'cusolver|^cusolver'
  'cusparse|^cusparse'
  'cufft|^cufft'
  'curand|^curand'
  'nvrtc|^nvrtc'
  'nvml|^nvml'
)
for specification in "${families[@]}"; do
  label="${specification%%|*}"
  pattern="${specification#*|}"
  exported="$(grep -Ec "$pattern" "$result_dir/flyt-exported-symbols.txt" || true)"
  required="$(grep -Ec "$pattern" "$result_dir/pytorch-cuda-undefined-symbols.txt" || true)"
  direct="$(grep -Ec "$pattern" "$result_dir/directly-interposed-symbols.txt" || true)"
  printf '%s exported=%s pytorch_undefined=%s direct_match=%s\n' \
    "$label" "$exported" "$required" "$direct"
done | tee "$result_dir/summary.txt"

printf '%s\n' \
  'NOTE: a non-interposed vendor API may still work through its lower-level CUDA driver calls; runtime tests remain authoritative.' \
  | tee -a "$result_dir/summary.txt"
printf 'explicit_client_stub_lines=%s\n' \
  "$(wc -l < "$result_dir/flyt-explicit-client-stubs.txt")" \
  | tee -a "$result_dir/summary.txt"
printf 'result_dir=%s\n' "$result_dir"
