#!/usr/bin/env bash
# Explicit future build only; this development stage did not execute builds.
set -euo pipefail
cd "$(dirname "$0")/../.."
target=${1:?usage: build.sh worker|cluster-manager|guest-artifacts IMAGE}
image=${2:?supply an image tag}
case "$target" in
  worker|cluster-manager|guest-artifacts) ;;
  *) echo "unsupported target; the stage-4 controller is reused" >&2; exit 2 ;;
esac
git archive --format=tar HEAD patches experiments/per-vm-worker experiments/controller experiments/hami-backend |
  docker build --file experiments/hami-backend/Containerfile --target "$target" --tag "$image" -
