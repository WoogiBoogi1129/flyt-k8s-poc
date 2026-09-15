#!/usr/bin/env bash

set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-podman}"
image="${PYTORCH_BUILD_IMAGE:-localhost/flyt-pytorch-builder:2.11.0-cu128}"
work_dir="${PYTORCH_WORK_DIR:-$root_dir/pytorch-work}"

if [[ ! -f /etc/subuid || ! -s /etc/subuid ]]; then
  printf '%s\n' \
    'rootless container builds require a configured /etc/subuid range' >&2
  exit 2
fi

command -v "$runtime" >/dev/null 2>&1 || {
  printf 'container runtime is missing: %s\n' "$runtime" >&2
  exit 1
}
install -d "$work_dir" "$root_dir/artifacts"

"$runtime" build --pull=missing -t "$image" \
  -f "$root_dir/pytorch/Containerfile" "$root_dir/pytorch"
"$runtime" run --rm \
  --security-opt label=disable \
  --volume "$root_dir/pytorch:/input:ro" \
  --volume "$work_dir:/work" \
  "$image"

find "$work_dir/wheels" -maxdepth 1 -type f -name 'torch-*.whl' \
  -exec cp -a {} "$root_dir/artifacts/" \;
cp -a "$work_dir/wheels/PYTORCH_COMMIT" \
  "$work_dir/wheels/BUILD_TOOLCHAIN" \
  "$work_dir/wheels/CCACHE_STATS" \
  "$work_dir/wheels/SHA256SUMS" \
  "$root_dir/artifacts/"

printf 'artifacts=%s\n' "$root_dir/artifacts"
