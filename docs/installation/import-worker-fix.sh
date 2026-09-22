#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run this script as root.' >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
archive="$repo_dir/.local/gpu-validation-20260921/worker-fsgroup.oci"
test -s "$archive"
/usr/bin/podman load -i "$archive"
/usr/bin/podman image inspect --format '{{.RepoDigests}}' localhost/flyt-worker:gpu-validation-20260922
