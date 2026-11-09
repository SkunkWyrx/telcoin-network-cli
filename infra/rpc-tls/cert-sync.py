#!/usr/bin/env python3
"""Publish or install the shared proxy TLS bundle using an Azure VM identity."""
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import socket
import time

ROOT = Path("/etc/telcoin/rpc-tls")
NAMES = ("fullchain.pem", "privkey.pem")


def run(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def validate(path, host):
    cert, key = (str(path / name) for name in NAMES)
    run("openssl", "verify", "-verify_hostname", host, "-untrusted", cert, cert)
    run("openssl", "x509", "-in", cert, "-noout", "-checkend", "86400")
    if run("openssl", "x509", "-in", cert, "-pubkey", "-noout") != run("openssl", "pkey", "-in", key, "-pubout"):
        raise ValueError("Certificate and key do not match")


def reload_and_verify(frontend, host, cert):
    run("docker", "exec", "telcoin-rpc-tls", "nginx", "-t", "-c", "/rpc-tls/nginx.conf")
    run("systemctl", "reload", "telcoin-rpc-tls")
    expected = ssl.PEM_cert_to_DER_cert(cert.split("-----END CERTIFICATE-----", 1)[0] + "-----END CERTIFICATE-----")
    context = ssl.create_default_context()
    for _ in range(10):
        try:
            with socket.create_connection((frontend, 443), timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname=host) as tls:
                    if tls.getpeercert(binary_form=True) == expected:
                        return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("Proxy did not serve the installed certificate")


def main(mode):
    os.umask(0o077)
    c = json.loads(Path("/etc/telcoin/cert-sync.json").read_text())
    vault, secret, host = (c[k] for k in ("vault", "secret", "hostname"))
    if not re.fullmatch(r"[a-z0-9-]{3,24}", vault) or not re.fullmatch(r"[a-zA-Z0-9-]+", secret):
        raise ValueError("Invalid vault configuration")
    token_uri = ("http://169.254.169.254/metadata/identity/oauth2/token"
                 "?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net")
    with urllib.request.urlopen(urllib.request.Request(token_uri, headers={"Metadata": "true"}), timeout=10) as response:
        token = json.load(response)["access_token"]
    url = f"https://{vault}.vault.azure.net/secrets/{secret}?api-version=7.4"
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def request(data=None):
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT" if data else "GET")
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)["value"]

    if mode == "publish":
        validate(ROOT, host)
        value = json.dumps({name: (ROOT / name).read_text() for name in NAMES})
        try:
            if request() == value:
                print("Certificate bundle already current.")
                return
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        request(json.dumps({"value": value, "contentType": "application/json"}).encode())
        print("Published the active certificate bundle.")
        return

    if mode != "pull":
        raise ValueError("Expected publish or pull")
    bundle = json.loads(request())
    if set(bundle) != set(NAMES) or not all(isinstance(bundle[n], str) and len(bundle[n]) < 24000 for n in NAMES):
        raise ValueError("Invalid certificate bundle")
    active = subprocess.run(["systemctl", "is-active", "--quiet", "telcoin-rpc-tls"]).returncode == 0
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        stage = Path(directory)
        old = {}
        for name in NAMES:
            path = ROOT / name
            old[name] = path.read_bytes() if path.exists() else None
            (stage / name).write_text(bundle[name])
        validate(stage, host)
        if all(old[name] == bundle[name].encode() for name in NAMES):
            return
        try:
            for name in NAMES:
                (stage / name).replace(ROOT / name)
            if active:
                reload_and_verify(c["frontend"], host, bundle[NAMES[0]])
        except Exception:
            for name in NAMES:
                if old[name] is None:
                    (ROOT / name).unlink(missing_ok=True)
                else:
                    (stage / name).write_bytes(old[name])
                    (stage / name).replace(ROOT / name)
            if active:
                run("systemctl", "reload", "telcoin-rpc-tls")
            raise
    print("Installed verified certificate bundle.")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except urllib.error.HTTPError as error:
        sys.exit(f"Certificate vault request failed: HTTP {error.code}")
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Certificate sync failed: {type(error).__name__}")
