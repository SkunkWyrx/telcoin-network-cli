#!/usr/bin/env bash
# Run as root on Observer 1. Pass gated-proof-nginx.conf on stdin.
set -euo pipefail
work=$(mktemp -d)
name="observer-gate-proof-${work##*.}"
fixture_pid=
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  if [[ -n "$fixture_pid" ]]; then kill "$fixture_pid" 2>/dev/null || true; fi
  rm -rf -- "$work"
}
trap cleanup EXIT
cat > "$work/nginx.conf"
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -subj /CN=adiri-orbs.oprail.com -addext subjectAltName=DNS:adiri-orbs.oprail.com \
  -keyout "$work/tls.key" -out "$work/tls.crt" 2>/dev/null
python3 - "$work" <<'PY' &
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = 404 if self.path != "/readyz" else 200 if (Path(sys.argv[1]) / "ready").exists() else 503
        self.send_response(code)
        self.end_headers()

HTTPServer(("127.0.0.1", 19090), Handler).serve_forever()
PY
fixture_pid=$!
image=${TLS_PROOF_IMAGE:-nginx:stable-alpine}
docker pull "$image" >/dev/null
image_id=$(docker image inspect --format '{{.Id}}' "$image")
docker run -d --name "$name" --network host \
  -v "$work:/proof:ro" "$image_id" nginx -c /proof/nginx.conf -g 'daemon off;' >/dev/null
request() {
  curl --noproxy '*' -sS --max-time 8 --cacert "$work/tls.crt" \
    --resolve adiri-orbs.oprail.com:18443:127.0.0.1 \
    -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"debug_traceTransaction","params":[]}' \
    -o "$work/response" -w '%{http_code}' https://adiri-orbs.oprail.com:18443/
}
for ((i=0; i<15; i++)); do
  if code=$(request 2>/dev/null) && [[ "$code" == 503 ]]; then break; fi
  sleep 2
done
[[ "${code:-}" == 503 ]]
kill -0 "$fixture_pid"
touch "$work/ready"
[[ "$(request)" == 200 ]]
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])).get("result")=="0x7e1"' "$work/response"
rm "$work/ready"
[[ "$(request)" == 503 ]]
echo 'Verified TLS with a real Adiri RPC upstream: simulated unready -> 503; ready -> 200; drain -> 503.'
echo 'Client debug method was replaced with the fixed eth_chainId proof request.'
