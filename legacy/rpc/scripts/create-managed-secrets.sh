#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
need kubectl
need openssl

[[ "$NAMESPACE" == flyt-* ]] || die "refusing non-Flyt namespace: $NAMESPACE"

namespace_part_of="$(kubectl get namespace "$NAMESPACE" \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/part-of}' 2>/dev/null || true)"
[[ "$namespace_part_of" == flyt ]] || \
  die "namespace $NAMESPACE must already exist with app.kubernetes.io/part-of=flyt"

if kubectl -n "$NAMESPACE" get secret flyt-mongodb-credentials >/dev/null 2>&1; then
  username="$(kubectl -n "$NAMESPACE" get secret flyt-mongodb-credentials \
    -o jsonpath='{.data.username}' | base64 -d)"
  password="$(kubectl -n "$NAMESPACE" get secret flyt-mongodb-credentials \
    -o jsonpath='{.data.password}' | base64 -d)"
  echo "secret=flyt-mongodb-credentials state=preserved"
else
  username=flytroot
  password="$(openssl rand -hex 32)"
  kubectl -n "$NAMESPACE" create secret generic flyt-mongodb-credentials \
    --from-literal=username="$username" \
    --from-literal=password="$password"
  echo "secret=flyt-mongodb-credentials state=created"
fi

if kubectl -n "$NAMESPACE" get secret flyt-cluster-manager-config >/dev/null 2>&1; then
  echo "secret=flyt-cluster-manager-config state=preserved"
  unset password
  exit 0
fi

config_file="$(mktemp)"
trap 'rm -f -- "$config_file"; unset password' EXIT
cat > "$config_file" <<EOF
[vm-resource-db]
host = "flyt-mongodb"
port = 27017
user = "$username"
password = "$password"
dbname = "flyt"

[ports]
node = 12401
client = 12402

[virt-server-auto-deallocate]
enabled = true
grace-period = 60

[ipc]
mqueue-path = "/tmp/flyt-rmgr-queue"
frontend-socket = "/run/flyt/flyt-frontend-socket"

[migration]
ckp-path = "/tmp/flyt-checkpoints"
EOF
chmod 0600 "$config_file"
kubectl -n "$NAMESPACE" create secret generic flyt-cluster-manager-config \
  --from-file=cluster-mgr.toml="$config_file"
echo "secret=flyt-cluster-manager-config state=created"
