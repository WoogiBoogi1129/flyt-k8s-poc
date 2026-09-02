#!/usr/bin/env bash

set -Eeuo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_config="$(mktemp)"
test_rendered="$(mktemp -d)"
trap 'rm -f -- "$test_config"; rm -rf -- "$test_rendered"' EXIT
cp "$root_dir/config.example.env" "$test_config"
sed -i \
  -e 's/REPLACE_WITH_GPU_NODE/gpu-worker-1/' \
  -e 's/REPLACE_WITH_PARENT_GPU_UUID/GPU-00000000-0000-0000-0000-000000000000/' \
  -e 's#REPLACE_WITH_PRIVATE_KEY_PATH#/tmp/flyt-test-key#' \
  -e 's#REPLACE_WITH_SSH_PUBLIC_KEY#ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyKeyDoNotUse flyt-test#' \
  "$test_config"
CONFIG_FILE="$test_config" RENDERED_DIR="$test_rendered" \
  "$root_dir/scripts/render-manifests.sh"
python3 - "$test_rendered" <<'PY'
import pathlib
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required for manifest validation") from exc

for path in sorted(pathlib.Path(sys.argv[1]).glob("*.yaml")):
    documents = [item for item in yaml.safe_load_all(path.read_text()) if item]
    if not documents:
        raise SystemExit(f"no Kubernetes objects in {path}")
    for document in documents:
        if not all(key in document for key in ("apiVersion", "kind", "metadata")):
            raise SystemExit(f"incomplete Kubernetes object in {path}")
print("manifest_yaml=PASS")
PY
