#!/usr/bin/env python3
"""Deterministic external Docker seam, NOT a container or live isolation proof."""
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / 'container.json'
args = sys.argv[1:]
with (ROOT / 'docker-calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
IMAGE = 'nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db'


def option(name):
    return args[args.index(name) + 1]


def run_fixture(container):
    mounts = {m['Destination']: Path(m['Source']) for m in container['Mounts'] if m['Type'] == 'bind'}
    envelope = json.loads((mounts['/inputs'] / 'assignment.json').read_text())
    assignment = envelope['assignment']
    # Exercise the actual per-attempt host spending UDS; no fixture bypass of admission.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(mounts['/model/capability.sock']))
        mode = (ROOT / 'mode').read_text() if (ROOT / 'mode').exists() else ''
        tools = [{'type': 'web_search'}] if mode == 'server-tool' else []
        text = 'Labeled deterministic Docker fixture only' + ('X' * 50000 if mode == 'large-request' else '')
        connection.sendall(json.dumps({'operation_id': envelope['run_id'] + '-fixture-call', 'reserve': 1,
            'payload': {'input': text, 'tools': tools, 'reasoning': {'effort': 'high'}}}).encode() + b'\n')
        response = bytearray()
        while not response.endswith(b'\n'):
            part = connection.recv(4096)
            if not part:
                break
            response.extend(part)
    reply = json.loads(response)
    if 'error' in reply:
        container['State']['ExitCode'] = 1
        return
    scratch = mounts['/scratch']
    loads, events = [], []
    for index, item in enumerate(envelope['inputs']):
        text = (mounts['/inputs'] / Path(item['path']).name).read_text()
        output = {'content': '\n'.join(f'{i + 1}|{line}' for i, line in enumerate(text.splitlines())), 'truncated': False}
        events.append({'reference': f'events:{index}', 'tool': 'read_file', 'args': {'path': item['path']}, 'result': json.dumps(output)})
        loads.append({'source': item['source'], 'sha256': item['sha256'], 'tool_reference': f'events:{index}'})
    (scratch / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
    (scratch / 'loads.json').write_text(json.dumps(loads))
    command = 'python -m unittest fixture'
    conversation = {'messages': [
        {'role': 'assistant', 'tool_calls': [{'id': 'fixture-test', 'function': {'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
        {'role': 'tool', 'tool_call_id': 'fixture-test', 'content': 'Labeled deterministic command/output, NOT live execution'}]}
    (scratch / 'conversation.json').write_text(json.dumps(conversation))
    (scratch / 'probes.json').write_text(json.dumps({'fixture': 'NOT live mount/network evidence'}))
    import hashlib
    content = 'Labeled deterministic stage fixture; not delivery or live isolation proof.'
    result = {'assignment_id': assignment['assignment_id'], 'claim_id': assignment['claim_id'],
        'handoff_digest': assignment['handoff_digest'], 'run_id': envelope['run_id'], 'status': 'done', 'loads': loads,
        'work': ['Labeled fixture retained skills and meaningful public lifecycle tests.'],
        'tests': [{'command': command, 'result': 'Labeled fixture pass'}],
        'artifacts': [{'name': 'stage-evidence', 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}]}
    mode = (ROOT / 'mode').read_text() if (ROOT / 'mode').exists() else ''
    if mode == 'malformed':
        result = {'status': 'done'}
    if mode == 'bad-load':
        loads[0]['tool_reference'] = 'invented'
        result['loads'] = loads
        (scratch / 'loads.json').write_text(json.dumps(loads))
    (scratch / 'result.json').write_text(json.dumps(result))
    if mounts['/workspace'] and container['Mounts'][0]['RW']:
        (mounts['/workspace'] / 'hello.py').write_text('print("candidate fixture")\n')
    if mode == 'symlink':
        (mounts['/workspace'] / 'escape').symlink_to('/home/ops/.hermes/auth.json')


if args[:2] == ['image', 'inspect']:
    print(json.dumps([{'Id': 'sha256:fixture-image', 'RepoDigests': [IMAGE], 'Config': {
        'Entrypoint': ['/opt/hermes/docker/entrypoint-dispatch.sh'], 'WorkingDir': '/opt/hermes', 'Volumes': {'/opt/data': {}}}}]))
elif args[0] == 'create':
    mounts = []
    for i, value in enumerate(args):
        if value == '--mount':
            parts = dict(part.split('=', 1) for part in args[i + 1].split(',') if '=' in part)
            mounts.append({'Type': 'bind', 'Source': parts['src'], 'Destination': parts['dst'],
                           'RW': 'readonly' not in args[i + 1], 'Propagation': 'rprivate'})
    tmpfs = {}
    for i, value in enumerate(args):
        if value == '--tmpfs':
            target, flags = args[i + 1].split(':', 1)
            tmpfs[target] = flags
    env = [args[i + 1] for i, value in enumerate(args) if value == '--env']
    container = {'Id': 'fixture-container', 'Image': 'sha256:fixture-image', 'Mounts': mounts,
        'Config': {'Image': IMAGE, 'User': option('--user'), 'Entrypoint': [option('--entrypoint')],
            'Cmd': ['/inputs/worker.py'], 'WorkingDir': '/opt/hermes', 'Env': env,
            'Labels': {'factory-v1.sandbox': option('--name')}},
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Init': True, 'Privileged': False,
            'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'], 'PidMode': '', 'IpcMode': 'private',
            'Memory': int(option('--memory')[:-1]) * 1024 * 1024, 'MemorySwap': int(option('--memory-swap')[:-1]) * 1024 * 1024,
            'NanoCpus': int(float(option('--cpus')) * 1_000_000_000), 'PidsLimit': int(option('--pids-limit')),
            'Tmpfs': tmpfs, 'RestartPolicy': {'Name': 'no'}},
        'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}
    if (ROOT / 'mode').exists() and (ROOT / 'mode').read_text() == 'extra-mount':
        container['Mounts'].append({'Type': 'volume', 'Destination': '/leak', 'RW': True})
    STATE.write_text(json.dumps(container))
    print('fixture-container')
elif args[0] == 'inspect':
    removal = ROOT / 'removal-in-progress'
    if removal.exists() and STATE.exists():
        if removal.read_text() == 'observed':
            STATE.unlink()
        else:
            removal.write_text('observed')
    if not STATE.exists():
        print('Error: No such object: fixture-container', file=sys.stderr)
        sys.exit(1)
    container = json.loads(STATE.read_text())
    if removal.exists():
        container['State']['Status'] = 'removing'
    if container['State']['Running'] and not ((ROOT / 'mode').exists() and (ROOT / 'mode').read_text() in ('hang', 'stop-race')):
        container['polls'] = container.get('polls', 0) + 1
        if container['polls'] >= 3:
            container['State']['Running'] = False
        STATE.write_text(json.dumps(container))
    print(json.dumps([container]))
elif args[0] == 'start':
    container = json.loads(STATE.read_text())
    if not ((ROOT / 'mode').exists() and (ROOT / 'mode').read_text() == 'stop-race'):
        run_fixture(container)
    container['State']['Running'] = True
    STATE.write_text(json.dumps(container))
    print('fixture-container')
elif args[0] == 'logs':
    if not STATE.exists():
        sys.exit(1)
    print('Labeled deterministic Docker fixture log. NOT live Hermes execution.')
elif args[0] == 'rm':
    removal = ROOT / 'removal-in-progress'
    if STATE.exists() and not removal.exists() and (ROOT / 'mode').exists() and (ROOT / 'mode').read_text() == 'stop-race':
        removal.write_text('pending')
        print('Error: removal of container fixture-container is already in progress', file=sys.stderr)
        sys.exit(1)
    STATE.unlink(missing_ok=True)
else:
    sys.exit(1)
