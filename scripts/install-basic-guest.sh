#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl
need scp
need sha256sum
need "$VIRTCTL"
need_config SSH_KEY
[[ -f "$SSH_KEY" ]] || die "SSH key is missing: $SSH_KEY"

artifact_dir="$ROOT_DIR/artifacts"
bundle="$artifact_dir/flyt-guest-bundle.tar.gz"
mkdir -p "$artifact_dir"
kubectl -n "$NAMESPACE" exec pod/flyt-builder -- test -f /workspace/BUILD_COMPLETE
kubectl -n "$NAMESPACE" cp \
  flyt-builder:/workspace/flyt-guest-bundle.tar.gz "$bundle"
expected="$(kubectl -n "$NAMESPACE" exec pod/flyt-builder -- \
  awk 'NR == 1 {print $1}' /workspace/flyt-guest-bundle.tar.gz.sha256)"
actual="$(sha256sum "$bundle" | awk '{print $1}')"
[[ "$actual" == "$expected" ]] || die "guest bundle checksum mismatch"

read -r -a requested_vms <<< "${INSTALL_VMS:-$VM_A}"
for vm_name in "${requested_vms[@]}"; do
  wait_for_vmi "$vm_name"
  vm_address="$(vmi_ip "$vm_name")"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null "$bundle" \
    "experiment@$vm_address:/opt/flyt-pytorch/"
  virt_ssh "$vm_name" '
    set -Eeuo pipefail
    sudo systemctl stop flyt-client-manager 2>/dev/null || true
    sudo ipcrm --all=msg 2>/dev/null || true
    sudo rm -f -- /tmp/flyt-client-mgr
    missing_packages=""
    for package in libelf1 libgomp1 libssl3; do
      dpkg-query -W "$package" >/dev/null 2>&1 || \
        missing_packages="$missing_packages $package"
    done
    if [[ -n "$missing_packages" ]]; then
      export DEBIAN_FRONTEND=noninteractive
      sudo -E apt-get -o Acquire::Languages=none update
      # shellcheck disable=SC2086
      sudo -E apt-get install -y --no-install-recommends $missing_packages
    fi
    # The containerDisk root is intentionally small; package indexes are
    # reproducible cache data and must not crowd out the Flyt client binaries.
    sudo apt-get clean
    sudo rm -rf -- /var/lib/apt/lists/*
    sudo install -d /etc/systemd/journald.conf.d
    printf "[Journal]\nSystemMaxUse=32M\nRuntimeMaxUse=32M\n" | \
      sudo tee /etc/systemd/journald.conf.d/flyt-vm.conf >/dev/null
    sudo journalctl --vacuum-size=32M >/dev/null 2>&1 || true
    sudo systemctl restart systemd-journald
    # Preserve Flyt crash dumps on the larger experiment disk before install.
    sudo mkdir -p /opt/flyt-pytorch/crash-archive /opt/flyt-pytorch/tmp
    sudo chmod 1777 /opt/flyt-pytorch/tmp
    grep -q " /tmp none bind " /etc/fstab || \
      echo "/opt/flyt-pytorch/tmp /tmp none bind,nofail 0 0" | sudo tee -a /etc/fstab >/dev/null
    mountpoint -q /tmp || sudo mount --bind /opt/flyt-pytorch/tmp /tmp
    sudo chmod 1777 /tmp
    sudo find /var/crash -maxdepth 1 -type f \
      -exec mv -t /opt/flyt-pytorch/crash-archive -- {} +
    sudo tar -C / -xzf /opt/flyt-pytorch/flyt-guest-bundle.tar.gz
    sudo chown experiment:experiment /opt/flyt-pytorch
    sudo systemctl daemon-reload
    sudo systemctl enable --now flyt-client-manager
    systemctl is-active flyt-client-manager
    test -x /opt/flyt-client/run-with-flyt
    test -x /opt/flyt-client/cap_probe
    test ! -e /dev/nvidia0
  '
  printf 'installed_vm=%s address=%s bundle_sha256=%s\n' \
    "$vm_name" "$vm_address" "$actual"
done
