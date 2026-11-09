#!/usr/bin/env python3
"""Sync-aware readiness. No Azure credentials or third-party packages required."""
import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def rpc(url, method, params=None):
    request = urllib.request.Request(url, data=json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or []
    }).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.load(response)
    if body.get('error') or body.get('result') is None:
        raise ValueError('RPC failed: ' + method)
    return body['result']


def evaluate(local, reference, expected_chain_id, max_lag=3, query=rpc):
    """A failed reference, wrong chain, or different hash is never healthy."""
    expected = int(str(expected_chain_id), 0)
    chains = [int(query(url, 'eth_chainId'), 16) for url in (local, reference)]
    if chains != [expected, expected]:
        return {'ready': False, 'reason': 'chain-id-mismatch'}
    heights = [int(query(url, 'eth_blockNumber'), 16) for url in (local, reference)]
    gap = heights[1] - heights[0]
    if abs(gap) > max_lag:
        return {'ready': False, 'reason': 'height-gap', 'gap': gap}
    number = hex(min(heights))
    blocks = [query(url, 'eth_getBlockByNumber', [number, False])
              for url in (local, reference)]
    matches = all(b.get('hash') and b.get('number') == number for b in blocks)
    matches = matches and blocks[0]['hash'] == blocks[1]['hash']
    return {'ready': bool(matches), 'reason': 'synced' if matches else 'hash-mismatch',
            'height': heights[0], 'referenceHeight': heights[1], 'gap': gap}


def effective(status, sampled_at, now, ttl, drained=False):
    if drained:
        return {**status, 'ready': False, 'reason': 'maintenance'}
    if sampled_at is None or now - sampled_at > ttl:
        return {'ready': False, 'reason': 'stale-health-sample'}
    return dict(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    interval = config.get('poll_seconds', 10)
    ttl = config.get('health_ttl_seconds', 30)
    if interval <= 0 or ttl <= interval or config.get('max_lag', 3) < 0:
        parser.error('Invalid readiness interval, TTL or lag')
    def sample():
        try:
            return evaluate(config.get('local_rpc', 'http://127.0.0.1:8545'),
                            config['reference_rpc'], config['chain_id'], config.get('max_lag', 3))
        except Exception as error:
            return {'ready': False, 'reason': 'rpc-unavailable', 'errorType': type(error).__name__}
    if args.once:
        result = sample()
        print(json.dumps(result))
        return 0 if result['ready'] else 1
    lock = threading.Lock()
    cache = {'status': {'ready': False, 'reason': 'starting'}, 'time': None}
    def collect():
        while True:
            result = sample()
            with lock:
                cache.update(status=result, time=time.monotonic())
            time.sleep(interval)
    threading.Thread(target=collect, daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/readyz', '/status'):
                self.send_error(404)
                return
            with lock:
                # /status is raw sync readiness for the local upgrade controller; it
                # deliberately ignores the maintenance flag, never the sample TTL.
                body = effective(cache['status'], cache['time'], time.monotonic(), ttl,
                                 self.path == '/readyz' and Path('/run/telcoin/draining').exists())
            payload = json.dumps(body).encode()
            self.send_response(200 if body['ready'] else 503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_):
            pass
    ThreadingHTTPServer(('0.0.0.0', 9090), Handler).serve_forever()


if __name__ == '__main__':
    raise SystemExit(main())
