#!/usr/bin/env python3
"""Certbot DNS-01 hook limited to the Adiri public RPC challenge zone."""
import json
import os
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HOST = "adiri-orbs.oprail.com"
ZONE = "_acme-challenge." + HOST
BASE = "https://management.azure.com"
RECORD = (BASE + "/subscriptions/8a149d4d-c52a-4432-929e-de754584e269"
          "/resourceGroups/oprail-observer-adiri/providers/Microsoft.Network"
          f"/dnsZones/{ZONE}/TXT/@?api-version=2018-05-01")
SERVERS = ("ns1-08.azure-dns.com", "ns2-08.azure-dns.net",
           "ns3-08.azure-dns.org", "ns4-08.azure-dns.info")


def identity_token():
    uri = ("http://169.254.169.254/metadata/identity/oauth2/token"
           "?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F")
    with urlopen(Request(uri, headers={"Metadata": "true"}), timeout=10) as response:
        return json.load(response)["access_token"]


def request(token, method, body=None, **headers):
    headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(RECORD, data=data, headers=headers, method=method), timeout=20) as response:
        content = response.read()
        return json.loads(content) if content else None


def cleanup(token, value):
    try:
        current = request(token, "GET")
        if current["properties"]["TXTRecords"] != [{"value": [value]}]:
            raise RuntimeError("Refusing to remove a TXT record with different contents")
        request(token, "DELETE", **{"If-Match": current["etag"]})
    except HTTPError as exc:
        if exc.code != 404:
            raise


def main():
    mode = sys.argv[1]
    host = os.getenv("CERTBOT_IDENTIFIER") or os.getenv("CERTBOT_DOMAIN")
    value = os.environ["CERTBOT_VALIDATION"]
    if host != HOST or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Unexpected Certbot challenge identifier or value")
    token = identity_token()
    if mode == "cleanup":
        cleanup(token, value)
    elif mode == "auth":
        payload = {"properties": {"TTL": 60, "TXTRecords": [{"value": [value]}]}}
        request(token, "PUT", payload, **{"If-None-Match": "*"})
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if all('"' + value + '"' in subprocess.run(
                    ["dig", "+time=3", "+tries=1", "+short", "@" + ns, ZONE, "TXT"],
                    capture_output=True, text=True, timeout=5, check=False,
                ).stdout.splitlines() for ns in SERVERS):
                    time.sleep(15)
                    return
                time.sleep(5)
            raise RuntimeError("Challenge did not reach all Azure nameservers")
        except Exception:
            cleanup(token, value)
            raise
    else:
        raise ValueError("Expected auth or cleanup")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        # Do not log bearer tokens or signed challenge material.
        sys.exit(f"Azure DNS hook failed: HTTP {error.code}")
