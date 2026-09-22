#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run this script as root.' >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
artifact_dir="$repo_dir/.local/gpu-validation-20260921"
for name in control-plane worker hook; do
  test -s "$artifact_dir/$name.oci"
done
for name in control-plane worker hook; do
  /usr/bin/podman load -i "$artifact_dir/$name.oci"
  /usr/bin/podman image inspect --format '{{.RepoDigests}}' "localhost/flyt-$name:gpu-validation-20260921"
done
# Dedicated, new directory only; never chmod/chown an existing unrelated tree.
for name in a b; do
  path="/var/lib/flyt-poc-20260921/$name"
  if [[ ! -e "$path" ]]; then
    install -d -m 0770 -o 107 -g 107 "$path"
  else
    stat -c '%n owner=%u:%g mode=%a' "$path"
  fi
done
echo 'Images loaded into rootful containers/storage; dedicated SHM directories prepared.'
