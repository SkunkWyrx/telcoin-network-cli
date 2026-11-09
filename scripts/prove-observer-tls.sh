#!/usr/bin/env bash
# Run on the observer VM as a user with Docker access. No listener on its NIC.
set -euo pipefail
work=$(mktemp -d)
name="observer-tls-proof-${work##*.}"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -subj /CN=adiri-orbs.oprail.com \
  -addext subjectAltName=DNS:adiri-orbs.oprail.com \
  -keyout "$work/tls.key" -out "$work/tls.crt" 2>/dev/null
cat > "$work/nginx.conf"
image=${TLS_PROOF_IMAGE:-nginx:stable-alpine}
docker pull "$image" >/dev/null
image_id=$(docker image inspect --format '{{.Id}}' "$image")
docker run -d --name "$name" --network host \
  -v "$work:/proof:ro" "$image_id" nginx -c /proof/nginx.conf -g 'daemon off;' >/dev/null
rpc() {
  curl --noproxy '*' -fsS --max-time 5 -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' "$@"
}
for ((i=0; i<30; i++)); do
  if response=$(rpc --cacert "$work/tls.crt" --resolve adiri-orbs.oprail.com:18443:127.0.0.1 \
      https://adiri-orbs.oprail.com:18443/ 2>"$work/curl-error"); then
    printf '%s\n' "$response" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("result") == "0x7e1", d; print("Verified HTTPS and Adiri chain ID:", d["result"])'
    # Record real node readiness without requiring sync to validate TLS.
    printf 'Observer readiness HTTP status: '
    curl --noproxy '*' -sS --max-time 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9090/readyz || true
    exit 0
  fi
  sleep 2
done
cat "$work/curl-error" >&2
docker logs "$name" >&2
exit 1
