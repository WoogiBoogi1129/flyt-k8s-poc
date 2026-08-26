#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
output="$(mktemp)"
trap 'rm -f -- "$output"' EXIT

kubectl kustomize "$root_dir/deploy/kustomize/overlays/shared-gpu12" > "$output"
python3 - "$output" <<'PY'
import sys
import yaml

docs = [doc for doc in yaml.safe_load_all(open(sys.argv[1])) if doc]
deployment = next(doc for doc in docs if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "flyt-gpu-cell")
claim = next(doc for doc in docs if doc["kind"] == "ResourceClaimTemplate")
assert deployment["spec"]["replicas"] == 0
env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
assert next(item for item in env if item["name"] == "FLYT_ALLOWED_GPU_UUIDS")["value"] == "RENDER_REQUIRED"
selector = claim["spec"]["spec"]["devices"]["requests"][0]["exactly"]["selectors"][0]["cel"]["expression"]
assert selector is False or selector == "false"
print("managed_kustomize_fail_closed=PASS")
PY
