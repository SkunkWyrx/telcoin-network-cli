#!/usr/bin/env python3
"""Isolated NGINX proof with a mock backend. Does not call the public RPC."""
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('keys', ROOT / 'infra/rpc-tls/api-key-sync.py')
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).strip()


def main():
    c = json.loads((ROOT / 'infra/pool/adiri.json').read_text())
    image = c['public_rpc']['proxy_image']
    suffix = uuid.uuid4().hex[:8]
    network, backend, proxy = ('rpc-key-proof-' + suffix + x for x in ('-net', '-backend', '-proxy'))
    entries = [{'id': 'a'*32, 'key': 'b'*64, 'tier': 'partner'},
               {'id': 'c'*32, 'key': 'd'*64, 'tier': 'partner'},
               {'id': 'e'*32, 'key': 'f'*64, 'tier': 'internal'},
               {'id': '1'*32, 'key': '2'*64, 'tier': 'partner', 'expires': 1},
               {'id': '3'*32, 'key': '4'*64, 'tier': 'internal', 'enabled': False}]
    limits = {'limits': {'anonymous': {'rate': 1, 'burst': 1}, 'partner': {'rate': 3, 'burst': 3},
                         'internal': {'rate': 5, 'burst': 5}, 'backend': {'rate': 100, 'burst': 100}}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'privkey.pem'),
                        '-out',str(root/'fullchain.pem'),'-days','1','-subj','/CN=localhost'],check=True,capture_output=True)
        config = (ROOT/'infra/rpc-tls/nginx-api-keys.conf.in').read_text()
        replacements = {'@FRONTEND_IP@':'0.0.0.0','@BACKEND_IP@':'127.0.0.1','@HOSTNAME@':'localhost'}
        for tier, value in limits['limits'].items(): replacements['@'+tier.upper()+'_BURST@'] = str(value['burst'])
        for name, value in replacements.items(): config = config.replace(name, value)
        config = config.replace('http://127.0.0.1:8545', 'http://'+backend+':8545')
        (root/'nginx.conf').write_text(config)
        (root/'api-keys.conf').write_text(keys.render(entries, limits))
        (root/'backend.py').write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length',0)))
  b=json.dumps({'jsonrpc':'2.0','id':1,'result':{'keyHeader':self.headers.get('X-API-Key')}}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*args): pass
HTTPServer(('0.0.0.0',8545),Handler).serve_forever()
''')
        try:
            docker('network','create',network)
            docker('run','-d','--name',backend,'--network',network,'-v',str(root)+':/proof:ro','python:3.12-alpine','python','/proof/backend.py')
            for _ in range(30):
                try:
                    docker('exec',backend,'python','-c',"import socket; s=socket.create_connection(('127.0.0.1',8545),1); s.close()")
                    break
                except subprocess.CalledProcessError: time.sleep(.2)
            else: raise RuntimeError('Mock backend did not start')
            docker('run','-d','--name',proxy,'--network',network,'-p','127.0.0.1::443','-v',str(root)+':/rpc-tls:ro',image,
                   'nginx','-c','/rpc-tls/nginx.conf','-g','daemon off;')
            port = docker('port',proxy,'443/tcp').rsplit(':',1)[1]
            context = ssl._create_unverified_context()
            def request(key=None):
                headers={'Content-Type':'application/json'}
                if key: headers['X-API-Key']=key
                req=urllib.request.Request('https://127.0.0.1:'+port+'/',data=b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}',headers=headers)
                try:
                    with urllib.request.urlopen(req,context=context,timeout=5) as r: return r.status, json.load(r)
                except urllib.error.HTTPError as e: return e.code, None
            for _ in range(30):
                try:
                    if request('not-a-key')[0] == 401: break
                except OSError: pass
                time.sleep(.2)
            else: raise RuntimeError('Isolated proxy did not start')
            for token in ['not-a-key','2'*64,'4'*64]: assert request(token)[0] == 401
            print('PASS invalid, expired, and revoked keys return 401')
            counts=[]
            for token in [None,'b'*64,'f'*64]:
                codes=[request(token)[0] for _ in range(16)]
                assert 429 in codes and all(c in (200,429) for c in codes), codes
                counts.append(codes.count(200))
            assert counts[0] < counts[1] < counts[2], counts
            print('PASS anonymous, partner, and internal tiers have increasing independent budgets:',counts)
            status,body=request('d'*64)
            assert status == 200 and body['result']['keyHeader'] is None
            print('PASS another partner key has its own budget; key header is stripped upstream')
            (root/'api-keys.conf').write_text(keys.render([k for k in entries if k['id']!='a'*32], limits))
            docker('exec',proxy,'nginx','-t','-c','/rpc-tls/nginx.conf')
            docker('kill','--signal=HUP',proxy)
            for _ in range(30):
                if request('b'*64)[0] == 401: break
                time.sleep(.1)
            else: raise AssertionError('Revoked key remained valid after reload')
            print('PASS revocation takes effect through a graceful reload')
            # Verify a shared cap across otherwise-valid keys, independently of per-key limits.
            limits['limits']['backend']={'rate':2,'burst':2}
            (root/'api-keys.conf').write_text(keys.render(entries, limits))
            (root/'nginx.conf').write_text(config.replace('zone=rpc_backend burst=100', 'zone=rpc_backend burst=2'))
            docker('exec',proxy,'nginx','-t','-c','/rpc-tls/nginx.conf')
            docker('kill','--signal=HUP',proxy)
            time.sleep(3)
            codes=[request(token)[0] for token in ['b'*64,'d'*64,'f'*64]*3]
            assert 429 in codes and 200 in codes, codes
            print('PASS aggregate backend cap remains enforced')
        except Exception:
            print('Isolated proxy diagnostics:', subprocess.run(['docker','logs','--tail','8',proxy],capture_output=True,text=True).stderr)
            raise
        finally:
            for container in [proxy,backend]:
                subprocess.run(['docker','rm','-f',container],capture_output=True)
            subprocess.run(['docker','network','rm',network],capture_output=True)


if __name__ == '__main__': main()
