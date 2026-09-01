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
mkdir -p "$artifact_dir"
bundle="$artifact_dir/flyt-guest-bundle.tar.gz"

kubectl -n "$NAMESPACE" exec pod/flyt-builder -- \
  test -f /workspace/BUILD_COMPLETE
kubectl -n "$NAMESPACE" cp \
  flyt-builder:/workspace/flyt-guest-bundle.tar.gz "$bundle"
kubectl -n "$NAMESPACE" exec pod/flyt-builder -- \
  cat /workspace/flyt-guest-bundle.tar.gz.sha256 \
  > "$bundle.sha256.builder"
expected_bundle_sha="$(awk 'NR == 1 {print $1}' "$bundle.sha256.builder")"
actual_bundle_sha="$(sha256sum "$bundle" | awk '{print $1}')"
[[ "$actual_bundle_sha" == "$expected_bundle_sha" ]] || \
  die "Flyt guest bundle checksum mismatch after kubectl cp"
printf '%s  %s\n' "$actual_bundle_sha" "$(basename "$bundle")" \
  > "$bundle.sha256"

wheel="${PYTORCH_WHEEL:-}"
if [[ -z "$wheel" ]]; then
  wheel="$(find "$ROOT_DIR/artifacts" -maxdepth 2 -type f \
    -name 'torch-*.whl' -print -quit)"
fi
[[ -n "$wheel" && -f "$wheel" ]] || \
  die 'set PYTORCH_WHEEL to the custom Flyt-compatible PyTorch wheel'
wheel_checksum_file="$(dirname "$wheel")/SHA256SUMS"
[[ -f "$wheel_checksum_file" ]] || \
  die "PyTorch checksum file is missing: $wheel_checksum_file"
(
  cd -- "$(dirname "$wheel")"
  sha256sum -c SHA256SUMS
)

read -r -a install_vms <<< "${INSTALL_VMS:-$VM_A $VM_B}"
(( ${#install_vms[@]} > 0 )) || die 'INSTALL_VMS selected no virtual machines'

for vm_name in "${install_vms[@]}"; do
  [[ "$vm_name" == "$VM_A" || "$vm_name" == "$VM_B" ]] || \
    die "INSTALL_VMS contains an unsupported virtual machine: $vm_name"
  wait_for_vmi "$vm_name"
  vm_address="$(vmi_ip "$vm_name")"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$bundle" "experiment@$vm_address:/tmp/flyt-guest-bundle.tar.gz"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$wheel" "experiment@$vm_address:/tmp/"
  scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$ROOT_DIR/pytorch/tests/compat_matrix.py" \
    "$ROOT_DIR/pytorch/tests/dynamic_training.py" \
    "$ROOT_DIR/pytorch/tests/quota_allocation.py" \
    "experiment@$vm_address:/opt/flyt-pytorch/"
  virt_ssh "$vm_name" '
    set -Eeuo pipefail
    sudo systemctl stop flyt-client-manager 2>/dev/null || true
    sudo ipcrm --all=msg 2>/dev/null || true
    sudo rm -f -- /tmp/flyt-client-mgr
    cleanup_apt() {
      sudo apt-get clean
      sudo rm -rf -- /var/lib/apt/lists/*
    }
    cleanup_all() {
      cleanup_apt
      rm -f -- /tmp/flyt-guest-bundle.tar.gz /tmp/torch-*.whl
    }
    trap cleanup_all EXIT
    cleanup_apt
    # The containerDisk root is only 2 GiB. Keep reproducible APT indexes and
    # package archives on the 20-GiB experiment disk mounted at /tmp.
    sudo install -d /tmp/apt-lists/partial /tmp/apt-cache/archives/partial
    apt_options=(
      -o Dir::State::lists=/tmp/apt-lists
      -o Dir::Cache=/tmp/apt-cache
      -o Acquire::Languages=none
    )
    sudo apt-get "${apt_options[@]}" update
    sudo DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" \
      install -y --no-install-recommends \
      libelf1 libgomp1 libssl3 python3-venv
    # Keep the compiler and development headers off the constrained root disk.
    # dpkg-deb extraction yields a reproducible relocatable toolchain used by
    # run-with-flyt for Triton/Inductor compilation.
    toolchain=/tmp/flyt-toolchain
    sudo rm -rf -- "$toolchain/root" "$toolchain/packages"
    sudo install -d "$toolchain/root" "$toolchain/packages"
    (
      cd "$toolchain/packages"
      sudo apt-get "${apt_options[@]}" download \
        gcc-11 cpp-11 libgcc-11-dev libc6-dev linux-libc-dev \
        libcrypt-dev rpcsvc-proto libc6 libgcc-s1 libisl23 libmpc3 \
        libmpfr6 libgmp10 zlib1g libzstd1 python3.10-dev libpython3.10-dev \
        libexpat1-dev
      for package in ./*.deb; do
        sudo dpkg-deb -x "$package" "$toolchain/root"
      done
    )
    sudo ln -sfn x86_64-linux-gnu-gcc-11 "$toolchain/root/usr/bin/gcc"
    sudo ln -sfn gcc "$toolchain/root/usr/bin/cc"
    sudo install -d "$toolchain/bin"
    printf "%s\n" \
      "#!/usr/bin/env bash" \
      "exec /tmp/flyt-toolchain/root/usr/bin/gcc --sysroot=/tmp/flyt-toolchain/root \"\$@\"" \
      | sudo tee "$toolchain/bin/gcc" >/dev/null
    sudo chmod 0755 "$toolchain/bin/gcc"
    sudo ln -sfn gcc "$toolchain/bin/cc"
    sudo tar -C / -xzf /tmp/flyt-guest-bundle.tar.gz
    sudo install -d /usr/local
    sudo ln -sfn /opt/flyt-pytorch/cuda /usr/local/cuda
    test -e /usr/local/cuda/lib64/libcudarto.so
    sudo install -d -o experiment -g experiment /opt/flyt-pytorch/tests
    sudo chown experiment:experiment \
      /opt/flyt-pytorch /opt/flyt-pytorch/tests
    mv /opt/flyt-pytorch/compat_matrix.py \
      /opt/flyt-pytorch/dynamic_training.py \
      /opt/flyt-pytorch/quota_allocation.py /opt/flyt-pytorch/tests/
    python3 -m venv /opt/flyt-pytorch/venv
    /opt/flyt-pytorch/venv/bin/pip install --no-cache-dir --upgrade pip
    /opt/flyt-pytorch/venv/bin/pip install --no-cache-dir numpy
    # Triton is large and the VM root filesystem is only 2 GiB. Install it on
    # the persistent 20-GiB experiment disk; run-with-flyt adds this target to
    # PYTHONPATH for normal VM development commands.
    sudo install -d -o experiment -g experiment /tmp/flyt-python-packages
    /opt/flyt-pytorch/venv/bin/pip install --no-cache-dir \
      --target /tmp/flyt-python-packages triton==3.7.1
    /opt/flyt-pytorch/venv/bin/pip install --no-cache-dir \
      /tmp/torch-*.whl
    sudo systemctl daemon-reload
    sudo systemctl enable flyt-client-manager
    sudo systemctl restart flyt-client-manager
    systemctl is-active flyt-client-manager
    test ! -e /dev/nvidia0
    smoke_rc=0
    timeout --signal=TERM --kill-after=5 120 \
      /opt/flyt-client/run-with-flyt \
      /opt/flyt-pytorch/venv/bin/python - \
      >/tmp/flyt-torch-smoke.log 2>&1 <<"PY" || smoke_rc=$?
import torch
print({
    "torch": torch.__version__,
    "cuda_build": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "device_count": torch.cuda.device_count(),
})
PY
    tail -40 /tmp/flyt-torch-smoke.log
    test "$smoke_rc" -eq 0
  '
done
