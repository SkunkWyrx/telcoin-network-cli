#!/usr/bin/env bash
# First-boot setup. Only empty, unpartitioned disks may be formatted.
set -euo pipefail
install -d -m 700 /var/lib/telcoin
install -d /var/lib/telcoin/db /var/lib/telcoin/consensus-db /var/lib/telcoin/static_files /mnt/telcoin-consensus

prepare_disk() {
  local lun="$1" target="$2" device="/dev/disk/azure/data/by-lun/$1" fs uuid
  for attempt in $(seq 1 60); do
    if [[ -b "$device" ]]; then break; fi
    # Older SCSI images may expose only the legacy stable LUN link.
    if [[ -b "/dev/disk/azure/scsi1/lun$lun" ]]; then
      device="/dev/disk/azure/scsi1/lun$lun"; break
    fi
    sleep 2
  done
  [[ -b "$device" ]] || { echo "Missing disk LUN $lun" >&2; exit 1; }
  # Do not hide existing data on the OS disk.
  if ! mountpoint -q "$target" && [[ -n "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Refusing to mount over nonempty directory: $target" >&2; exit 1
  fi
  fs=$(blkid -s TYPE -o value "$device" || true)
  if [[ -z "$fs" ]]; then
    # Partition tables and unknown signatures must never be overwritten.
    [[ -z "$(wipefs --no-act --noheadings --output TYPE "$device")" ]] || { echo "Unexpected disk signature: $device" >&2; exit 1; }
    [[ $(lsblk -n -o TYPE "$device" | wc -l) -eq 1 ]] || { echo "Disk has partitions: $device" >&2; exit 1; }
    mkfs.ext4 "$device"
  elif [[ "$fs" != ext4 ]]; then
    echo "Expected ext4 on $device, found $fs" >&2; exit 1
  fi
  uuid=$(blkid -s UUID -o value "$device")
  if ! grep -q "^UUID=$uuid " /etc/fstab; then
    printf 'UUID=%s %s ext4 defaults,noatime,nofail,x-systemd.device-timeout=60s 0 2\n' "$uuid" "$target" >> /etc/fstab
  fi
  mountpoint -q "$target" || mount "$target"
}
prepare_disk 0 /var/lib/telcoin/db
prepare_disk 1 /mnt/telcoin-consensus
install -d /mnt/telcoin-consensus/consensus-db /mnt/telcoin-consensus/static_files
for dir in consensus-db static_files; do
  target="/var/lib/telcoin/$dir"
  if ! mountpoint -q "$target" && [[ -n "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Refusing to mount over nonempty directory: $target" >&2; exit 1
  fi
  if ! grep -q "^/mnt/telcoin-consensus/$dir " /etc/fstab; then
    printf '/mnt/telcoin-consensus/%s %s none bind,nofail,x-systemd.requires-mounts-for=/mnt/telcoin-consensus 0 0\n' "$dir" "$target" >> /etc/fstab
  fi
  mountpoint -q "$target" || mount "$target"
done
systemctl daemon-reload
