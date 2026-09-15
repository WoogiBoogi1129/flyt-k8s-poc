#!/usr/bin/env bash
# Explicit future build only. No build, test or patch check ran during development.
set -euo pipefail
cd "$(dirname "$0")/../.."
target=${1:?usage: build.sh worker|cluster-manager|guest-artifacts OUTPUT_IMAGE}
image=${2:?supply output image tag}
case "$target" in
  worker|cluster-manager|guest-artifacts) ;;
  *) echo 'Unknown target; existing Controller/CRDs are reused' >&2; exit 2 ;;
esac
git archive --format=tar HEAD patches experiments/per-vm-worker experiments/controller experiments/hami-backend experiments/session-control-plane |
  docker build --file experiments/session-control-plane/Containerfile --target "$target" --tag "$image" -
