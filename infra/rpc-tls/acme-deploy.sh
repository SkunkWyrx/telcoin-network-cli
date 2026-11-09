#!/usr/bin/env bash
set -euo pipefail
umask 077
dest=/etc/telcoin/rpc-tls
lineage=${RENEWED_LINEAGE:?missing Certbot lineage}
host=adiri-orbs.oprail.com
[[ "$lineage" == /var/lib/oprail-acme/config/live/adiri-orbs.oprail.com ]]
openssl x509 -in "$lineage/fullchain.pem" -noout -checkhost "$host" -checkend 86400
[[ "$(openssl x509 -in "$lineage/fullchain.pem" -pubkey -noout)" == "$(openssl pkey -in "$lineage/privkey.pem" -pubout)" ]]
work=$(mktemp -d "$dest/.deploy.XXXXXX")
rollback=false
cleanup() {
  if [[ "$rollback" == true ]]; then
    mv -f "$work/old-cert" "$dest/fullchain.pem"
    mv -f "$work/old-key" "$dest/privkey.pem"
    systemctl reload telcoin-rpc-tls || true
  fi
  rm -rf -- "$work"
}
trap cleanup EXIT
cp -L "$dest/fullchain.pem" "$work/old-cert"
cp -L "$dest/privkey.pem" "$work/old-key"
install -m 0600 "$lineage/fullchain.pem" "$work/new-cert"
install -m 0600 "$lineage/privkey.pem" "$work/new-key"
rollback=true
mv -f "$work/new-cert" "$dest/fullchain.pem"
mv -f "$work/new-key" "$dest/privkey.pem"
docker exec telcoin-rpc-tls nginx -t -c /rpc-tls/nginx.conf
systemctl reload telcoin-rpc-tls
expected=$(openssl x509 -in "$lineage/fullchain.pem" -noout -fingerprint -sha256)
for attempt in {1..10}; do
  served=$(timeout 5 openssl s_client -connect 20.12.235.224:443 -servername "$host" -verify_hostname "$host" -verify_return_error </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null || true)
  if [[ "$served" == "$expected" ]]; then
    rollback=false
    echo 'Renewed certificate verified on the live proxy.'
    exit 0
  fi
  sleep 1
done
echo 'Renewed certificate did not become active; restoring previous files.' >&2
exit 1
