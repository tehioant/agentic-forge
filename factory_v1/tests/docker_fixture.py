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
    check_path = mounts['/inputs'] / 'checks.json'
    if check_path.exists():
        check = json.loads(check_path.read_text())
        mode = (ROOT / 'mode').read_text() if (ROOT / 'mode').exists() else ''
        exit_code = 1 if mode == 'checks-fail' else 0
        container['State']['ExitCode'] = exit_code
        container['check_report'] = {'run_id': check['run_id'], 'records': [
            {'command': value, 'exit_code': exit_code, 'stdout': 'Labeled Docker CHECK fixture, not live execution',
             'stderr': '', 'output_truncated': False} for value in check['commands']]}
        return
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
    if mounts['/workspace'] and container['Mounts'][0]['RW'] and assignment['handoff']['stage'] != 'simplify' and mode != 'implementation-no-op':
        (mounts['/workspace'] / 'hello.py').write_text('print("candidate fixture")\n')
    if mode == 'symlink':
        (mounts['/workspace'] / 'escape').symlink_to('/home/ops/.hermes/auth.json')
    if assignment['handoff']['stage'] == 'simplify' and mode != 'malformed':
        contract = assignment['handoff']['simplification']
        if mode in ('cleanup', 'false-no-op', 'applied-finding'):
            (mounts['/workspace'] / 'hello.py').write_text('print("candidate fixture")  \n')
        if mode == 'scope-violation':
            (mounts['/workspace'] / 'unrelated.py').write_text('unapproved = True\n')
        tree = {}
        for path in mounts['/workspace'].rglob('*'):
            if path.is_file():
                raw = path.read_bytes()
                tree[str(path.relative_to(mounts['/workspace']))] = {
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                    'executable': bool(path.stat().st_mode & 0o111)}
        revision = hashlib.sha256(json.dumps(tree, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        findings = [{'kind': 'behavior', 'path': 'hello.py:1', 'evidence': 'Labeled risky proposal fixture',
                     'proposal': 'Do not apply: change output.'}] if mode in ('findings', 'applied-finding', 'false-approval') else []
        report = {'contract': contract['contract'], 'adaptation': assignment['handoff']['adaptation'],
            'input_candidate_sha256': contract['input_candidate_sha256'], 'candidate_sha256': revision,
            'outcome': 'cleanup' if mode == 'cleanup' else ('findings' if findings else 'no-op'),
            'behavior_preservation': 'not-established' if findings else 'preserved',
            'angles': {angle: 'Labeled ' + angle + ' search/work fixture' for angle in contract['angles']},
            'findings': findings}
        if mode == 'wrong-revision':
            report['candidate_sha256'] = '0' * 64
        if mode == 'wrong-input':
            report['input_candidate_sha256'] = '0' * 64
        if mode == 'wrong-adaptation':
            report['adaptation'] = 'unreviewed'
        if mode == 'missing-angle':
            del report['angles']['altitude']
        if mode == 'behavior-not-preserved':
            report['behavior_preservation'] = 'not-established'
        if mode == 'false-approval':
            report.update(outcome='no-op', behavior_preservation='preserved')
        content = json.dumps(report, sort_keys=True)
        if mode == 'malformed-stage':
            content = '{not JSON'
        if mode == 'nested-stage':
            content = '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        if mode == 'duplicate-stage':
            content = content[:-1] + ', "outcome": "no-op"}'
        result['artifacts'].append({'name': 'simplification-result', 'content': content,
                                    'sha256': hashlib.sha256(content.encode()).hexdigest()})
    if 'review' in assignment['handoff'] and mode != 'malformed':
        contract = assignment['handoff']['review']
        findings = []
        if mode in ('review-reject', 'review-gate', 'review-security', 'review-test', 'review-false-pass'):
            kind = {'review-gate': 'gate-weakening', 'review-security': 'security-weakening', 'review-test': 'test-weakening'}.get(mode, 'missing')
            findings = [{'id': 'R1', 'kind': kind, 'path': 'hello.py:1',
                         'requirement': 'Fixture requirement: print required greeting.',
                         'evidence': 'Labeled omitted behavior despite passing check fixture.',
                         'correction': 'Implement required greeting and meaningful regression.'}]
        report = {'contract': contract['contract'], 'axis': contract['axis'],
                  'adaptation': assignment['handoff']['adaptation'], 'pins': contract['pins'],
                  'verdict': 'reject' if findings else 'pass', 'findings': findings,
                  'test_assessment': 'Labeled requirement-based legitimate test-change assessment; not live review.',
                  'stuckness': None}
        if mode == 'review-stuck':
            result['status'] = 'stuck'
            report.update(verdict='stuck', stuckness={k: 'Labeled actual diagnostic uncertainty fixture' for k in
                          ('attempts', 'evidence', 'uncertainty', 'recommendation')})
        if mode == 'review-wrong-axis':
            report['axis'] = 'combined'
        if mode == 'review-wrong-tree':
            report['pins'] = {**contract['pins'], 'tree_sha256': '0' * 64}
        if mode == 'review-wrong-adaptation':
            report['adaptation'] = 'unreviewed'
        if mode == 'review-false-pass':
            report['verdict'] = 'pass'
        if mode == 'review-duplicate-finding':
            finding = {'id': 'same', 'kind': 'missing', 'path': 'hello.py', 'requirement': 'required',
                       'evidence': 'fixture', 'correction': 'fix'}
            report.update(verdict='reject', findings=[finding, finding])
        if mode == 'review-write':
            (mounts['/workspace'] / 'hello.py').write_text('forged read-only mutation')
        content = json.dumps(report, sort_keys=True)
        if mode == 'review-malformed':
            content = '{not JSON'
        if mode == 'review-duplicate':
            content = content[:-1] + ', "verdict":"pass"}'
        result['artifacts'].append({'name': 'review-result', 'content': content,
                                    'sha256': hashlib.sha256(content.encode()).hexdigest()})
    (scratch / 'result.json').write_text(json.dumps(result))


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
            STATE.unlink(missing_ok=True)
        else:
            removal.write_text('observed')
    if not STATE.exists():
        print('Error: No such object: fixture-container', file=sys.stderr)
        sys.exit(1)
    try:
        container = json.loads(STATE.read_text())
    except FileNotFoundError:
        print('Error: No such object: fixture-container', file=sys.stderr)
        sys.exit(1)
    if removal.exists():
        container['State']['Status'] = 'removing'
    mode = (ROOT / 'mode').read_text() if (ROOT / 'mode').exists() else ''
    check_hang = mode == 'checks-hang' and 'check_report' in container
    if container['State']['Running'] and mode not in ('hang', 'stop-race') and not check_hang:
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
    container = json.loads(STATE.read_text())
    print(json.dumps(container['check_report']) if 'check_report' in container else
          'Labeled deterministic Docker fixture log. NOT live Hermes execution.')
elif args[0] == 'rm':
    removal = ROOT / 'removal-in-progress'
    if STATE.exists() and (ROOT / 'mode').exists() and (ROOT / 'mode').read_text() == 'stop-race':
        if not removal.exists():
            removal.write_text('pending')
        print('Error: removal of container fixture-container is already in progress', file=sys.stderr)
        sys.exit(1)
    STATE.unlink(missing_ok=True)
else:
    sys.exit(1)
