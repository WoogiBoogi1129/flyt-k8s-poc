#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
need kubectl
need jq

need_config FLYT_GPU_UUIDS
need_config FLYT_CLUSTER_MANAGER_IMAGE
need_config FLYT_GPU_CELL_IMAGE

read -r -a gpu_uuids <<< "$FLYT_GPU_UUIDS"
(( ${#gpu_uuids[@]} >= 1 && ${#gpu_uuids[@]} <= 2 )) || \
  die "FLYT_GPU_UUIDS must contain one or two UUIDs"

declare -A seen=()
selector_parts=()
for uuid in "${gpu_uuids[@]}"; do
  [[ $uuid =~ ^GPU-[A-Fa-f0-9-]+$ ]] || die "invalid GPU UUID: $uuid"
  [[ -z ${seen[$uuid]:-} ]] || die "duplicate GPU UUID: $uuid"
  seen[$uuid]=1

  # Read-only ownership gate. A GPU is rejected if any allocated ResourceClaim
  # currently consumes that exact DRA device or one of its MIG children.
  device_name="$(kubectl get resourceslices -o json | jq -r --arg uuid "$uuid" '
    .items[].spec.devices[]?
    | select(.attributes.uuid.string == $uuid)
    | .name' | head -n 1)"
  [[ -n $device_name ]] || die "GPU $uuid is not published by NVIDIA DRA"
  conflicts="$(kubectl get resourceclaims -A -o json | jq -r --arg device "$device_name" '
    .items[]
    | select(any(.status.allocation.devices.results[]?;
        .device == $device or (.device | startswith($device + "-mig-"))))
    | [.metadata.namespace, .metadata.name] | @tsv')"
  [[ -z $conflicts ]] || die "GPU $uuid is already represented by allocated claims: $conflicts"

  selector_parts+=("device.attributes['gpu.nvidia.com'].uuid == '$uuid'")
done

output_dir="${MANAGED_RENDERED_DIR:-$ROOT_DIR/deploy/rendered/managed}"
rm -rf -- "$output_dir"
mkdir -p "$output_dir"
cp -a "$ROOT_DIR/deploy/kustomize/base" "$output_dir/base"
cp -a "$ROOT_DIR/deploy/kustomize/overlays/shared-gpu12" "$output_dir/overlay"

selector="${selector_parts[0]}"
if (( ${#selector_parts[@]} == 2 )); then
  selector="(${selector_parts[0]}) || (${selector_parts[1]})"
fi

python3 - "$output_dir/overlay/gpu-cells.yaml" "$selector" <<'PY'
import pathlib
import sys
import yaml

path = pathlib.Path(sys.argv[1])
selector = sys.argv[2]
docs = list(yaml.safe_load_all(path.read_text()))
docs[0]["spec"]["spec"]["devices"]["requests"][0]["exactly"]["selectors"][0]["cel"]["expression"] = selector
path.write_text("---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs))
PY

python3 - "$output_dir/overlay/gpu-cell-local-values.yaml" "${#gpu_uuids[@]}" <<'PY'
import pathlib
import sys
import yaml

path = pathlib.Path(sys.argv[1])
doc = yaml.safe_load(path.read_text())
doc["spec"]["replicas"] = int(sys.argv[2])
path.write_text(yaml.safe_dump(doc, sort_keys=False))
PY

python3 - "$output_dir/overlay/gpu-cells.yaml" "$FLYT_GPU_UUIDS" <<'PY'
import pathlib
import sys
import yaml

path = pathlib.Path(sys.argv[1])
allowed = sys.argv[2]
docs = list(yaml.safe_load_all(path.read_text()))
container = docs[1]["spec"]["template"]["spec"]["containers"][0]
entry = next(item for item in container["env"] if item["name"] == "FLYT_ALLOWED_GPU_UUIDS")
entry["value"] = allowed
path.write_text("---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs))
PY

python3 - "$output_dir/overlay/kustomization.yaml" \
  "$FLYT_CLUSTER_MANAGER_IMAGE" "$FLYT_GPU_CELL_IMAGE" <<'PY'
import pathlib
import sys
import yaml

path = pathlib.Path(sys.argv[1])
doc = yaml.safe_load(path.read_text())
refs = {
    "registry.invalid/flyt/cluster-manager": sys.argv[2],
    "registry.invalid/flyt/gpu-cell": sys.argv[3],
}
for image in doc["images"]:
    ref = refs[image["name"]]
    image.pop("newTag", None)
    if "@" in ref:
        name, digest = ref.rsplit("@", 1)
        if not digest.startswith("sha256:"):
            raise SystemExit(f"unsupported image digest: {ref}")
        image["newName"] = name
        image["digest"] = digest
    else:
        name, sep, tag = ref.rpartition(":")
        if not sep or "/" in tag:
            raise SystemExit(f"image must include a tag or digest: {ref}")
        image["newName"] = name
        image["newTag"] = tag
path.write_text(yaml.safe_dump(doc, sort_keys=False))
PY

# Repoint the copied overlay at its copied base.
sed -i 's#../../base#../base#' "$output_dir/overlay/kustomization.yaml"
kubectl kustomize "$output_dir/overlay" > "$output_dir/all.yaml"
printf 'managed_overlay=%s gpu_count=%s\n' "$output_dir/all.yaml" "${#gpu_uuids[@]}"
