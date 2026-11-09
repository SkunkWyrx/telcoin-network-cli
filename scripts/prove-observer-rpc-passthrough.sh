#!/usr/bin/env bash
# Run on Observer 1 with proof-nginx.conf on stdin. No public ingress.
set -euo pipefail
work=$(mktemp -d)
name="observer-rpc-proof-${work##*.}"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf -- "$work"' EXIT
cat > "$work/nginx.conf"
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -subj /CN=adiri-orbs.oprail.com -addext subjectAltName=DNS:adiri-orbs.oprail.com \
  -keyout "$work/tls.key" -out "$work/tls.crt" 2>/dev/null
image=${TLS_PROOF_IMAGE:-nginx:stable-alpine}
docker pull "$image" >/dev/null
docker run -d --name "$name" --network host -v "$work:/proof:ro" \
  "$(docker image inspect --format '{{.Id}}' "$image")" nginx -c /proof/nginx.conf -g 'daemon off;' >/dev/null
python3 - "$work/tls.crt" <<'PY'
import http.client, json, ssl, sys, time
cert = ssl.create_default_context(cafile=sys.argv[1])

class LoopbackTLS(http.client.HTTPSConnection):
    def connect(self):
        conn = self._create_connection(("127.0.0.1", 18443), self.timeout)
        self.sock = self._context.wrap_socket(conn, server_hostname=self.host)

def rpc(method, params, request_id, tls):
    connection = (LoopbackTLS("adiri-orbs.oprail.com", context=cert, timeout=12)
                  if tls else http.client.HTTPConnection("127.0.0.1", 8545, timeout=12))
    try:
        connection.request("POST", "/", json.dumps(dict(jsonrpc="2.0", id=request_id, method=method, params=params)),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"{method}: HTTP {response.status}")
        result = json.loads(response.read())
        if result.get("id") != request_id:
            raise RuntimeError(f"{method}: response ID did not match")
        return result
    finally:
        connection.close()

for attempt in range(25):
    try:
        chain = rpc("eth_chainId", [], "tls-chain", True)
        assert chain.get("result") == "0x7e1", chain
        break
    except (OSError, RuntimeError):
        if attempt == 24:
            raise
        time.sleep(2)

block = rpc("eth_getBlockByNumber", ["latest", False], "pick-block", False)["result"]
txs = block.get("transactions", [])
if txs:
    method, params = "debug_traceTransaction", [txs[0], {"tracer": "callTracer", "timeout": "5s"}]
else:
    # Fixed block selector keeps the comparison stable while the observer syncs.
    method, params = "debug_traceBlockByHash", [block["hash"], {"tracer": "callTracer", "timeout": "5s"}]
debug_ok = False
try:
    direct = rpc(method, params, "direct-debug", False)
    proxied = rpc(method, params, "tls-debug", True)
    if "result" in direct and "result" in proxied and direct["result"] == proxied["result"]:
        print(f"PASS: {method} matched direct RPC for block {int(block['number'], 16)}.")
        debug_ok = True
    else:
        print(f"FAIL: {method}: direct error={direct.get('error')}; TLS error={proxied.get('error')}")
except (RuntimeError, OSError) as exc:
    print(f"FAIL: {method}: {exc}")
direct = rpc("eth_sendRawTransaction", ["0x"], "direct-invalid-tx", False)
proxied = rpc("eth_sendRawTransaction", ["0x"], "tls-invalid-tx", True)
assert "error" in direct and direct["error"] == proxied.get("error"), (direct, proxied)
print(f"PASS: invalid transaction was rejected identically over TLS and direct RPC: {proxied['error']}")
print("No valid transaction was sent; validator forwarding was not tested.")
if not debug_ok:
    sys.exit(1)
PY
