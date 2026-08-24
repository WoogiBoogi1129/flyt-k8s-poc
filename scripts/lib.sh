#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

CONFIG_FILE="${CONFIG_FILE:-$ROOT_DIR/config.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1091
  source "$CONFIG_FILE"
fi

NAMESPACE="${FLYT_NAMESPACE:-flyt-system}"
NODE_NAME="${NODE_NAME:-}"
GPU_UUID="${GPU_UUID:-}"
GPU_MODE="${GPU_MODE:-mig}"
VM_A="${VM_A:-flyt-vm-a}"
VM_B="${VM_B:-flyt-vm-b}"
SSH_KEY="${SSH_KEY:-}"
VIRTCTL="${VIRTCTL:-virtctl}"
RENDERED_DIR="${RENDERED_DIR:-$ROOT_DIR/deploy/rendered}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

need_config() {
  local name="$1" value="${!1:-}"
  [[ -n "$value" && "$value" != REPLACE_* ]] || \
    die "set $name in config.env (copy config.example.env first)"
}

need_cluster_config() {
  need_config NODE_NAME
  need_config GPU_UUID
}

vmi_ip() {
  local vm_name="$1" ip_address
  ip_address="$(kubectl -n "$NAMESPACE" get vmi "$vm_name" \
    -o jsonpath='{.status.interfaces[0].ipAddress}')"
  [[ "$ip_address" =~ ^[0-9a-fA-F:.]+$ ]] || \
    die "invalid or unavailable VMI IP for $vm_name: $ip_address"
  printf '%s\n' "$ip_address"
}

virt_ssh() {
  local vm_name="$1" command="$2"
  need_config SSH_KEY
  [[ -f "$SSH_KEY" ]] || die "SSH key is missing: $SSH_KEY"
  "$VIRTCTL" ssh \
    -i "$SSH_KEY" \
    --known-hosts=/dev/null \
    "--local-ssh-opts=-o StrictHostKeyChecking=no" \
    --command="$command" \
    "experiment@vm/$vm_name/$NAMESPACE"
}

wait_for_vmi() {
  local vm_name="$1"
  kubectl -n "$NAMESPACE" wait --for=condition=Ready \
    "vmi/$vm_name" --timeout=10m
  virt_ssh "$vm_name" \
    'cloud-init status --wait; test -f /var/lib/cloud/instance/flyt-ready'
}
