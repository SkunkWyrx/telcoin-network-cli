#!/usr/bin/env python3
"""Run outside the observer VNet from the client allowed by the test rule."""
import argparse
import http.client
import json
import socket
import ssl
import time


def connect(host, port, timeout=4):
    return socket.create_connection((host, port), timeout)


def require_closed(host, port):
    try:
        with connect(host, port):
            raise SystemExit(f"FAIL: TCP accepted at {host}:{port}")
    except (TimeoutError, ConnectionRefusedError, ConnectionResetError):
        print(f"PASS: no TCP connection at {host}:{port}")


def rpc(hostname, frontend, ca_file):
    context = ssl.create_default_context(cafile=ca_file)

    class FrontendHTTPS(http.client.HTTPSConnection):
        def connect(self):
            with_raw = connect(frontend, 443)
            try:
                self.sock = context.wrap_socket(with_raw, server_hostname=hostname)
            except Exception:
                with_raw.close()
                raise

    client = FrontendHTTPS(hostname, timeout=8)
    try:
        request = {"jsonrpc": "2.0", "id": "public-proof", "method": "eth_chainId", "params": []}
        client.request("POST", "/", json.dumps(request), {"Content-Type": "application/json"})
        response = client.getresponse()
        body = json.loads(response.read(65536))
        if response.status != 200 or body.get("id") != request["id"] or body.get("result") != "0x7e1":
            raise SystemExit(f"FAIL: unexpected Adiri RPC response (HTTP {response.status}): {body}")
        print(f"PASS: verified TLS for {hostname} via {frontend} and Adiri chain ID 0x7e1")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("ready", "drained", "recovered"))
    parser.add_argument("--hostname", default="adiri-orbs.oprail.com")
    parser.add_argument("--frontend", required=True, help="Static frontend IP (tests before DNS cutover)")
    parser.add_argument("--direct", action="append", default=[], help="Public observer IP to test; repeat per VM")
    parser.add_argument("--ca-file", help="Explicit test CA; omit for production public trust")
    args = parser.parse_args()
    if args.phase == "drained":
        # Run this phase only after the backend is marked down and all other
        # test backends are excluded. Every connection here is a fresh flow.
        for _ in range(3):
            require_closed(args.frontend, 443)
            time.sleep(1)
    else:
        rpc(args.hostname, args.frontend, args.ca_file)
    for address in args.direct:
        for port in (443, 8545, 8546, 18443):
            require_closed(address, port)


if __name__ == "__main__":
    main()
