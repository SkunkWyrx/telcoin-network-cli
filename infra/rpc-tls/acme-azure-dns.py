#!/usr/bin/env python3
"""Certbot DNS-01 hook limited to the Adiri public RPC challenge zones."""
import json
import os
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DNS_SUFFIXES = {
    "adiri-orbs.oprail.com": "08",
    "rpc-adiri.oprail.com": "05",
}
BASE = "https://management.azure.com"


def identity_token():
    uri = ("http://169.254.169.254/metadata/identity/oauth2/token"
           "?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F")
    with urlopen(Request(uri, headers={"Metadata": "true"}), timeout=10) as response:
        return json.load(response)["access_token"]


def request(record, token, method, body=None, **headers):
    headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(record, data=data, headers=headers, method=method), timeout=20) as response:
        content = response.read()
        return json.loads(content) if content else None


def cleanup(record, token, value):
    try:
        current = request(record, token, "GET")
        if current["properties"]["TXTRecords"] != [{"value": [value]}]:
            raise RuntimeError("Refusing to remove a TXT record with different contents")
        request(record, token, "DELETE", **{"If-Match": current["etag"]})
    except HTTPError as exc:
        if exc.code != 404:
            raise


def main():
    mode = sys.argv[1]
    host = os.getenv("CERTBOT_IDENTIFIER") or os.getenv("CERTBOT_DOMAIN")
    value = os.environ["CERTBOT_VALIDATION"]
    if host not in DNS_SUFFIXES or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Unexpected Certbot challenge identifier or value")
    zone = "_acme-challenge." + host
    suffix = DNS_SUFFIXES[host]
    servers = tuple(f"ns{i}-{suffix}.azure-dns.{tld}"
                    for i, tld in enumerate(("com", "net", "org", "info"), 1))
    record = (BASE + "/subscriptions/8a149d4d-c52a-4432-929e-de754584e269"
              "/resourceGroups/oprail-observer-adiri/providers/Microsoft.Network"
              f"/dnsZones/{zone}/TXT/@?api-version=2018-05-01")
    token = identity_token()
    if mode == "cleanup":
        cleanup(record, token, value)
    elif mode == "auth":
        payload = {"properties": {"TTL": 60, "TXTRecords": [{"value": [value]}]}}
        request(record, token, "PUT", payload, **{"If-None-Match": "*"})
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if all('"' + value + '"' in subprocess.run(
                    ["dig", "+time=3", "+tries=1", "+short", "@" + ns, zone, "TXT"],
                    capture_output=True, text=True, timeout=5, check=False,
                ).stdout.splitlines() for ns in servers):
                    time.sleep(15)
                    return
                time.sleep(5)
            raise RuntimeError("Challenge did not reach all Azure nameservers")
        except Exception:
            cleanup(record, token, value)
            raise
    else:
        raise ValueError("Expected auth or cleanup")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        # Do not log bearer tokens or signed challenge material.
        sys.exit(f"Azure DNS hook failed: HTTP {error.code}")
