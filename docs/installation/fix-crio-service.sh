#!/usr/bin/env bash
set -Eeuo pipefail
# A minor-version transition can recreate CRI-O sandboxes. On a single-node
# control plane, expect temporary API/Pod interruption; run during maintenance.
[[ $EUID -eq 0 ]] || { echo 'Run this script as root.' >&2; exit 1; }
/usr/bin/crio --version | head -1 | grep -q '1.37.'
backup_dir=$(mktemp -d /root/crio-before-1.37.XXXXXX)
cp -a /etc/crio "$backup_dir/"
systemctl cat crio > "$backup_dir/crio-service.txt"
dropin=/etc/systemd/system/crio.service.d/99-flyt-packaged-binary.conf
if [[ -e "$dropin" ]]; then
  echo "Existing override: $dropin; inspect before running again." >&2
  exit 1
fi
install -d /etc/systemd/system/crio.service.d
cat > "$dropin" <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/crio $CRIO_CONFIG_OPTIONS $CRIO_RUNTIME_OPTIONS $CRIO_STORAGE_OPTIONS $CRIO_NETWORK_OPTIONS $CRIO_METRICS_OPTIONS
EOF
systemctl daemon-reload
echo 'Restarting CRI-O; minor-version changes can recreate Kubernetes Pod sandboxes.'
if ! systemctl restart crio; then
  rm "$dropin"
  systemctl daemon-reload
  systemctl restart crio
  echo "Restart failed; previous service restored. Backup: $backup_dir" >&2
  exit 1
fi
systemctl is-active crio
systemctl show crio -p ExecStart -p MainPID
echo "Backup: $backup_dir"
echo "Rollback: remove $dropin, then systemctl daemon-reload && systemctl restart crio"
