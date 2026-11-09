#!/usr/bin/env bash
set -euo pipefail
source /etc/telcoin/image.conf
for dir in /var/lib/telcoin/db /var/lib/telcoin/consensus-db /var/lib/telcoin/static_files; do
  mountpoint -q "$dir" || { echo "Required mount missing: $dir" >&2; exit 1; }
done
# Retry identity/RBAC propagation after a new deployment.
logged_in=false
for attempt in $(seq 1 30); do
  if /usr/local/sbin/telcoin-registry-login "$REGISTRY" && docker pull "$IMAGE"; then
    logged_in=true; break
  fi
  sleep 10
done
[[ "$logged_in" == true ]] || exit 1
exec docker run --rm --name telcoin-observer --network host \
  --env-file /etc/telcoin/observer.env \
  --mount type=bind,src=/etc/telcoin/entrypoint.sh,dst=/app/entrypoint.sh,readonly \
  --mount type=bind,src=/var/lib/telcoin,dst=/var/lib/telcoin \
  --mount type=bind,src=/var/lib/telcoin/db,dst=/var/lib/telcoin/db \
  --mount type=bind,src=/var/lib/telcoin/consensus-db,dst=/var/lib/telcoin/consensus-db \
  --mount type=bind,src=/var/lib/telcoin/static_files,dst=/var/lib/telcoin/static_files \
  "$IMAGE"
