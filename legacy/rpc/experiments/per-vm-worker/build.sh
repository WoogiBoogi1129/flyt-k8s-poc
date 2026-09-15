#!/usr/bin/env bash
# Build only committed, allowlisted inputs. Run explicitly after validation resumes.
set -euo pipefail
cd "$(dirname "$0")/../.."
target=${1:?usage: build.sh worker|cluster-manager|guest-artifacts IMAGE_TAG}
image=${2:?supply an image tag}
case "$target" in
  worker|cluster-manager|guest-artifacts) ;;
  *) echo "unsupported target" >&2; exit 2 ;;
esac
git archive --format=tar HEAD patches experiments/per-vm-worker |
  docker build --file experiments/per-vm-worker/Containerfile --target "$target" --tag "$image" -
