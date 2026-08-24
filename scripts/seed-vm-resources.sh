#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need kubectl

sm_a="${SM_A:-46}"
sm_b="${SM_B:-46}"
memory_a_mib="${MEMORY_A_MIB:-8192}"
memory_b_mib="${MEMORY_B_MIB:-8192}"
ip_a="$(vmi_ip "$VM_A")"
ip_b="$(vmi_ip "$VM_B")"

for value in "$sm_a" "$sm_b" "$memory_a_mib" "$memory_b_mib"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "resource values must be positive integers"
done

kubectl -n "$NAMESPACE" exec deploy/flyt-mongodb -- env \
  VM_A_IP="$ip_a" VM_B_IP="$ip_b" NODE_NAME="$NODE_NAME" \
  SM_A="$sm_a" SM_B="$sm_b" \
  MEMORY_A_MIB="$memory_a_mib" MEMORY_B_MIB="$memory_b_mib" \
  /bin/bash -lc '
    set -Eeuo pipefail
    mongosh --quiet \
      --username "$MONGO_INITDB_ROOT_USERNAME" \
      --password "$MONGO_INITDB_ROOT_PASSWORD" \
      --authenticationDatabase admin \
      --eval '\''
        const c = db.getSiblingDB("flyt").vm_required_resources;
        c.deleteMany({
          host_ip: process.env.NODE_NAME,
          vm_ip: {$nin: [process.env.VM_A_IP, process.env.VM_B_IP]}
        });
        for (const item of [
          {vm_ip: process.env.VM_A_IP, compute_units: Number(process.env.SM_A), memory: Number(process.env.MEMORY_A_MIB)},
          {vm_ip: process.env.VM_B_IP, compute_units: Number(process.env.SM_B), memory: Number(process.env.MEMORY_B_MIB)}
        ]) {
          c.updateOne(
            {vm_ip: item.vm_ip},
            {$set: {vm_ip: item.vm_ip, host_ip: process.env.NODE_NAME,
                    compute_units: item.compute_units, memory: item.memory}},
            {upsert: true}
          );
        }
        printjson(c.find({}, {_id: 0}).sort({vm_ip: 1}).toArray());
      '\''
  '

printf 'seeded_vm_a=%s sm=%s memory_mib=%s\n' "$ip_a" "$sm_a" "$memory_a_mib"
printf 'seeded_vm_b=%s sm=%s memory_mib=%s\n' "$ip_b" "$sm_b" "$memory_b_mib"
