#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
need kubectl

manifest="${MANAGED_MANIFEST:-$ROOT_DIR/deploy/rendered/managed/all.yaml}"
[[ "$NAMESPACE" == flyt-* ]] || die "refusing non-Flyt namespace: $NAMESPACE"
[[ -s "$manifest" ]] || die "render managed manifests first: $manifest"

namespace_part_of="$(kubectl get namespace "$NAMESPACE" \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/part-of}' 2>/dev/null || true)"
[[ "$namespace_part_of" == flyt ]] || \
  die "namespace $NAMESPACE must exist with app.kubernetes.io/part-of=flyt"

# The apply set must contain only namespaced Flyt-owned objects. Namespace,
# CRD, ClusterRole, and arbitrary cluster-scoped mutations are rejected.
python3 - "$manifest" "$NAMESPACE" <<'PY'
import sys
import yaml

path, namespace = sys.argv[1:]
allowed = {
    "ConfigMap", "Secret", "Service", "StatefulSet", "Deployment",
    "NetworkPolicy", "ResourceClaimTemplate", "PersistentVolumeClaim",
}
for doc in yaml.safe_load_all(open(path)):
    if not doc:
        continue
    kind = doc.get("kind")
    meta = doc.get("metadata", {})
    if kind not in allowed:
        raise SystemExit(f"unsafe kind in managed apply set: {kind}")
    if meta.get("namespace") != namespace:
        raise SystemExit(f"object outside namespace {namespace}: {kind}/{meta.get('name')}")
    labels = meta.get("labels", {})
    if labels.get("app.kubernetes.io/part-of") != "flyt":
        raise SystemExit(f"object lacks Flyt ownership label: {kind}/{meta.get('name')}")
PY

kubectl apply --server-side --field-manager=flyt-repository -f "$manifest"
