#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
image=${1:?usage: build.sh REGISTRY/flyt/controller:stage4}
git archive --format=tar HEAD controllers/flyt experiments/gpu-request |
  docker build --file experiments/gpu-request/Containerfile --target controller --tag "$image" -
