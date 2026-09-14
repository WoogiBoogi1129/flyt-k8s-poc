#!/usr/bin/env bash
# Explicit future build command; no build was run during implementation.
set -euo pipefail
cd "$(dirname "$0")/../.."
target=${1:?usage: build.sh controller|worker|cluster-manager|guest-artifacts IMAGE}
image=${2:?supply an image tag}
case "$target" in
  controller) recipe=controllers/flyt/Containerfile ;;
  worker|cluster-manager|guest-artifacts) recipe=experiments/controller/Containerfile ;;
  *) echo "unsupported target" >&2; exit 2 ;;
esac
git archive --format=tar HEAD patches experiments/per-vm-worker experiments/controller controllers/flyt |
  docker build --file "$recipe" --target "$target" --tag "$image" -
