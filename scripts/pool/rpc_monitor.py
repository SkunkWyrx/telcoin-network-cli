#!/usr/bin/env python3
"""Sample the public Adiri RPC and publish stateful alert signals."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from health import rpc

PUBLIC = 'https://rpc-adiri.oprail.com'
REFERENCE = 'https://rpc.adiri.tel'
CHAIN = 2017
STATE = Path('/var/lib/oprail-rpc-monitor/state.json')
RESOURCE = ('/subscriptions/8a149d4d-c52a-4432-929e-de754584e269'
            '/resourceGroups/oprail-observer-adiri/providers/Microsoft.Compute'
            '/virtualMachines/adiri-observer-runner')


def head(endpoint):
    if int(rpc(endpoint, 'eth_chainId'), 16) != CHAIN:
        raise ValueError('Unexpected chain ID')
    return int(rpc(endpoint, 'eth_blockNumber'), 16)


def sample():
    try:
        current = head(PUBLIC)
    except Exception as error:
        logging.warning('Public Adiri RPC failed: %s', type(error).__name__)
        return {'OutageIncident': True}
    signals = {'OutageIncident': False}
    try:
        signals['LagIncident'] = head(REFERENCE) - current >= 5
    except Exception as error:
        logging.warning('Reference Adiri RPC failed: %s', type(error).__name__)
    return signals


def publish(metrics):
    args = urlencode({'api-version': '2018-02-01', 'resource': 'https://monitoring.azure.com/'})
    with urlopen(Request('http://169.254.169.254/metadata/identity/oauth2/token?' + args,
                         headers={'Metadata': 'true'}), timeout=8) as response:
        token = json.load(response)['access_token']
    timestamp = datetime.now(timezone.utc).isoformat()
    for name, value in metrics.items():
        payload = {'time': timestamp, 'data': {'baseData': {
            'metric': name, 'namespace': 'Oprail.AdiriRPC', 'dimNames': [],
            'series': [{'dimValues': [], 'min': value, 'max': value, 'sum': value, 'count': 1}],
        }}}
        request = Request('https://centralus.monitoring.azure.com' + RESOURCE + '/metrics',
                          data=json.dumps(payload).encode(),
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=8):
            pass


def main():
    now = datetime.now(timezone.utc).timestamp()
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    streaks = previous.get('streaks', {}) if 0 < now - previous.get('time', 0) <= 90 else {}
    signals = sample()
    metrics = {}
    for name, bad in signals.items():
        streaks[name] = min(streaks.get(name, 0) + 1, 3) if bad else 0
        metrics[name] = int(streaks[name] >= 3)
    for name in set(streaks) - signals.keys():
        del streaks[name]
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'time': now, 'streaks': streaks}))
    temporary.replace(STATE)
    publish(metrics)
    print(json.dumps(metrics))


if __name__ == '__main__':
    main()
