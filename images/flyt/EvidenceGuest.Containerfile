# The input disk is copied from the digest-pinned Ubuntu containerDisk in the
# reproduction procedure and resized offline to 16 GiB with qemu-img. Its
# SHA256 is recorded before building. Cloud-init grows the root partition.
FROM scratch
COPY --chown=107:107 --chmod=0440 disk.qcow2 /disk/disk.qcow2
