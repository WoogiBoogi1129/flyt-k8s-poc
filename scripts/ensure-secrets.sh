#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need openssl

secret_name=flyt-mongodb-credentials
if kubectl -n "$NAMESPACE" get secret "$secret_name" >/dev/null 2>&1; then
  printf 'secret=%s state=preserved\n' "$secret_name"
  exit 0
fi

username=flytroot
password="$(openssl rand -hex 32)"
kubectl -n "$NAMESPACE" create secret generic "$secret_name" \
  --from-literal="username=$username" \
  --from-literal="password=$password" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset password
printf 'secret=%s state=created\n' "$secret_name"
