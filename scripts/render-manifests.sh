#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
need rg

config_file="$CONFIG_FILE"
[[ -f "$config_file" ]] || die "missing config.env; copy config.example.env"

required=(
  FLYT_NAMESPACE NODE_NAME GPU_UUID EXPECTED_GPU_SM STORAGE_CLASS
  SSH_PUBLIC_KEY VM_CONTAINERDISK_IMAGE
  FLYT_REPOSITORY FLYT_COMMIT PYTORCH_REPOSITORY PYTORCH_COMMIT RUST_TOOLCHAIN
)
for name in "${required[@]}"; do
  need_config "$name"
done

[[ "$FLYT_NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || \
  die "FLYT_NAMESPACE is not a valid DNS label"
[[ "$NODE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid NODE_NAME"
[[ "$GPU_UUID" =~ ^GPU-[A-Fa-f0-9-]+$ ]] || die "invalid GPU_UUID"
[[ "$EXPECTED_GPU_SM" =~ ^[1-9][0-9]*$ ]] || die "invalid EXPECTED_GPU_SM"
[[ "$FLYT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "FLYT_COMMIT must be a full SHA"
[[ "$PYTORCH_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "PYTORCH_COMMIT must be a full SHA"
[[ "$SSH_PUBLIC_KEY" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-) ]] || \
  die "SSH_PUBLIC_KEY must be an OpenSSH public key"

mkdir -p "$RENDERED_DIR"
for template in "$ROOT_DIR"/deploy/templates/*.yaml; do
  output="$RENDERED_DIR/$(basename "$template")"
  content="$(<"$template")"
  for name in "${required[@]}"; do
    token="__${name}__"
    content="${content//"$token"/${!name}}"
  done
  printf '%s\n' "$content" > "$output"
done

unexpected="$(rg -n '__[A-Z][A-Z_]+__' "$RENDERED_DIR" \
  | rg -v '__MONGO_(USER|PASSWORD)__' || true)"
[[ -z "$unexpected" ]] || die "unrendered template tokens:\n$unexpected"
printf 'rendered_dir=%s\n' "$RENDERED_DIR"
