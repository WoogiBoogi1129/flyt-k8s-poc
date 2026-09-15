#!/usr/bin/env bash
# Future explicit build command. No build or patch check was run during authoring.
set -euo pipefail
cd "$(dirname "$0")/../.."
target=${1:?usage: build.sh probe-artifacts|worker-diagnostic OUTPUT_IMAGE [STAGE5_WORKER_IMAGE@sha256:DIGEST]}
image=${2:?supply output image tag}
args=()
case "$target" in
  probe-artifacts) ;;
  worker-diagnostic)
    base=${3:?supply a built stage-5 Worker image by digest}
    if [[ ! "$base" =~ @sha256:[0-9a-f]{64}$ ]]; then
      echo 'Stage-5 Worker digest is required' >&2
      exit 2
    fi
    args+=(--build-arg "STAGE5_WORKER_IMAGE=$base")
    ;;
  *) echo 'Unknown build target' >&2; exit 2 ;;
esac
git archive --format=tar HEAD experiments/hami-e2e |
  docker build --file experiments/hami-e2e/Containerfile --target "$target" --tag "$image" "${args[@]}" -
