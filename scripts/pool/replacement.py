"""Replace a fleet with newly provisioned nodes while keeping its original count."""
import json
from pathlib import Path
import re
import secrets
import shlex
import uuid

from policy import pinned_image, upgrade_order


def replace_fresh(fleet, image, template):
    image = pinned_image(image)
    expected = fleet.c['registry'] + '.azurecr.io/' + fleet.c['repository'] + '@'
    if not image.startswith(expected):
        raise ValueError('Replacement image is outside this network release repository')
    if not fleet.c['nodes']:
        raise ValueError('Bootstrap the first observer before replacing a fleet')
    print('Fresh database rollout: add one blank-disk observer, wait for sync, then retire one old observer.')
    if not fleet.apply:
        return
    template = Path(template).resolve()
    if not template.is_file():
        raise ValueError('Configure the network node template first')
    path = fleet.state / 'replacement.json'
    state = json.loads(path.read_text()) if path.exists() else None
    if state and state['status'] != 'completed' and state['image'] != image:
        raise ValueError('Resume the active replacement with the same image before starting another')
    if not state or state['status'] == 'completed':
        generation = uuid.uuid4().hex[:8]
        prefix = fleet.c.get('node_name_prefix', fleet.c['network'] + '-observer-')
        dns = fleet.c.get('dns_label_prefix', 'oprail-' + fleet.c['network'] + '-observer-')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,35}', prefix) or not re.fullmatch(r'[a-z][a-z0-9-]{0,35}', dns):
            raise ValueError('Configure safe node and DNS prefixes')
        state = {'image': image, 'status': 'running', 'original_count': len(fleet.c['nodes']), 'steps': []}
        for old in upgrade_order(fleet.c['nodes']):
            suffix = str(old['ordinal']) + '-' + generation
            name = prefix + suffix
            env_file = fleet.state / (name + '.env')
            env_file.write_text('source ' + shlex.quote(str(template)) + '\nVM_NAME=' + name +
                                '\nVM_DNS_LABEL=' + dns + suffix + '\nTN_BLS_PASSPHRASE=' + secrets.token_hex(32) + '\n')
            env_file.chmod(0o600)
            state['steps'].append({'old': old, 'name': name, 'env_file': str(env_file), 'stage': 'planned'})
        fleet.save('replacement.json', state)
    for step in state['steps']:
        if step['stage'] == 'completed':
            continue
        new = next((n for n in fleet.c['nodes'] if n['name'] == step['name']), None)
        if not new:
            if step['stage'] != 'planned':
                raise ValueError('Provisioning was interrupted. Inspect the recorded replacement VM before resuming')
            step['stage'] = 'provisioning'
            fleet.save('replacement.json', state)
            fleet.add(step['env_file'], image, fresh=True)
            new = next(n for n in fleet.c['nodes'] if n['name'] == step['name'])
        fleet.wait_synced(new)
        if fleet.c.get('public_rpc'):
            fleet.install_public_rpc(new)
            if step['old']['name'] == fleet.c['public_rpc']['issuer']:
                fleet.handoff_issuer(step['old'], new)
        # Recheck after proxy installation and certificate handoff before destructive work.
        fleet.wait_synced(new)
        step['stage'] = 'retiring'
        fleet.save('replacement.json', state)
        fleet.remove(step['old']['name'])
        step['stage'] = 'completed'
        fleet.save('replacement.json', state)
    if len(fleet.c['nodes']) != state['original_count']:
        raise ValueError('Replacement count differs from the original fleet; inspect inventory')
    state['status'] = 'completed'
    fleet.save('replacement.json', state)
    print('Fresh database rollout completed with', state['original_count'], 'observers.')
