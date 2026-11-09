#!/usr/bin/env bash
# Run on the drained recovery VM through Azure Run Command. Verify rollback
# snapshot completion outside the VM before invoking. Does not promote a release.
set -euo pipefail
candidate=${1:?digest-pinned candidate required}
original=${2:?current digest-pinned image required}
config=/etc/telcoin/image.conf
source "$config"
pattern='^[a-z0-9.-]+/[a-z0-9./_-]+@sha256:[a-f0-9]{64}$'
[[ "$candidate" =~ $pattern && "$original" =~ $pattern && "$candidate" != "$original" ]]
[[ "$candidate" == "$REGISTRY/"* && "$IMAGE" == "$original" ]]
for dir in /var/lib/telcoin/db /var/lib/telcoin/consensus-db /var/lib/telcoin/static_files; do
  mountpoint -q "$dir"
done

install -d -m 755 /run/telcoin
# Keep this marker set until an independent readiness check passes.
touch /run/telcoin/draining
systemctl stop telcoin-observer
sync
/usr/local/sbin/telcoin-registry-login "$REGISTRY" >/dev/null
docker pull "$candidate" >/dev/null
backup=$(mktemp /etc/telcoin/image.conf.pre-recovery.XXXXXX)
cp -p "$config" "$backup"
temp=$(mktemp /etc/telcoin/image.conf.new.XXXXXX)
trap 'rm -f -- "$temp"' EXIT
printf 'REGISTRY=%s\nIMAGE=%s\n' "$REGISTRY" "$candidate" > "$temp"
mv -f -- "$temp" "$config"
systemctl start telcoin-observer
echo "Recovery candidate configured: $candidate"
echo "Original image configuration preserved: $backup"
echo 'Node is drained; confirm actual sync before admitting traffic.'
