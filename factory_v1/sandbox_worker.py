"""Runs only inside the reviewed container. No host auth, controller or GitHub imports."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, '/inputs')
from sandbox_relay import ResponsesRelay


def main():
    os.umask(0o022)
    envelope = json.loads(Path('/inputs/assignment.json').read_text())
    assignment = envelope['assignment']
    handoff = assignment['handoff']
    run_id = envelope['run_id']
    os.chdir('/workspace')
    for path in ('/scratch/tmp', '/scratch/home/.hermes'):
        Path(path).mkdir(parents=True, exist_ok=True)
    # Keep all six admitted file/terminal tools direct; discovery wrappers are not capabilities.
    subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'config', 'set',
                    'tools.tool_search.enabled', 'off'], check=True, capture_output=True, timeout=30)
    from model_tools import handle_function_call
    from run_agent import AIAgent

    events = Path('/scratch/events.jsonl').open('a', buffering=1)
    def record(name, args, result):
        reference = 'events:' + str(record.count)
        record.count += 1
        events.write(json.dumps({'reference': reference, 'tool': name, 'args': args, 'result': result}) + '\n')
        return reference
    record.count = 0

    def tool(name, args):
        result = handle_function_call(name, args, task_id=run_id)
        return result, record(name, args, result)

    def denied(result):
        parsed = json.loads(result)
        if not parsed.get('error') and not parsed.get('not_found'):
            raise RuntimeError('isolation_probe_failed')

    # Probe denial BEFORE constructing the agent so file-mutation warnings cannot enter its context.
    probes = {}
    for path in ('/home/ops/.hermes/auth.json', '/var/run/docker.sock', '/root/.ssh/id_rsa', '/inputs/assignment.json'):
        if path.startswith('/inputs'):
            result, _ = tool('write_file', {'path': path, 'content': 'DENIED'})
            denied(result)
            probes['immutable-input-write'] = result
        else:
            result, _ = tool('read_file', {'path': path})
            denied(result)
            probes[path] = result
    try:
        Path('/inputs/.factory-denied-shell-write').write_text('DENIED')
        probes['immutable-shell-write'] = 'UNEXPECTED_SUCCESS'
    except OSError as error:
        probes['immutable-shell-write'] = type(error).__name__
    if 'write_workspace' not in handoff['capabilities']:
        result, _ = tool('write_file', {'path': '/workspace/.factory-denied-write', 'content': 'DENIED'})
        denied(result)
        probes['review-file-write'] = result
        try:
            Path('/workspace/.factory-denied-shell-write').write_text('DENIED')
            probes['review-shell-write'] = 'UNEXPECTED_SUCCESS'
        except OSError as error:
            probes['review-shell-write'] = type(error).__name__
    for host, port in [('172.17.0.1', 80), ('169.254.169.254', 80), ('140.82.112.3', 443)]:
        try:
            with socket.create_connection((host, port), timeout=1):
                probes[host] = 'UNEXPECTED_SUCCESS'
        except OSError as error:
            probes[host] = type(error).__name__
    Path('/scratch/probes.json').write_text(json.dumps(probes))
    if any(value == 'UNEXPECTED_SUCCESS' for value in probes.values()):
        raise RuntimeError('isolation_probe_failed')
    loads, instructions = [], []
    for item in envelope['inputs']:
        # Actual Hermes file-tool calls in this executing process, not mounted-file assertions.
        value, reference = tool('read_file', {'path': item['path'], 'limit': 2000})
        raw = Path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise RuntimeError('immutable_input_changed')
        parsed = json.loads(value)
        if parsed.get('error') or parsed.get('truncated') or parsed.get('not_found'):
            raise RuntimeError('instruction_load_incomplete')
        loads.append({'source': item['source'], 'sha256': item['sha256'], 'tool_reference': reference})
        instructions.append({'source': item['source'], 'path': item['path'], 'tool_result': parsed})
    Path('/scratch/loads.json').write_text(json.dumps(loads))
    relay = ResponsesRelay('/model/capability.sock', handoff['profile']['reasoning'])
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    agent = AIAgent(model='gpt-6.1-sol', provider='custom', api_mode='codex_responses',
        base_url=f'http://127.0.0.1:{relay.server_port}/v1', api_key='unused-local-broker',
        enabled_toolsets=['terminal', 'file'], quiet_mode=True, save_trajectories=False,
        skip_context_files=True, skip_memory=True, skip_background_review=True,
        fallback_model=None, checkpoints_enabled=False, cwd='/workspace',
        max_iterations=envelope['limits']['max_calls'], run_budget_seconds=envelope['limits']['seconds'],
        reasoning_config={'effort': handoff['profile']['reasoning']})
    prompt = ('Run the selected installed entry-point skills for only this bounded assignment. '
              'The following are actual executing-context Hermes read_file outputs of exact immutable inputs. '
              'Use their instructions with the explicit stage adaptation and support policy. '
              'Baseline is /inputs/baseline, exact submitted source is /workspace (no Git credentials or metadata). '
              'No nested review, delegation, commits, publication, merge, fallback or new spending. '
              'Run meaningful tests through the agreed public seams. Review/diagnosis may copy source to /scratch to test. '
              'Retain work, test commands and actual outputs. Return ONLY JSON with status (done/blocked/stuck), '
              'work (nonempty strings), tests (nonempty objects with command and result strings), and artifacts '
              '(objects name/content/sha256, including stage-evidence). Each tests.command must match the complete '
              'native terminal command verbatim, including compound shell commands; do not split or shorten it. '
              'Each tests.result must be a nonempty string containing actual output and exit status, not an object. '
              'Compute each artifact sha256 from the exact UTF-8 content with a terminal tool. Do not claim delivery.\n' + json.dumps({
                  'assignment_id': assignment['assignment_id'], 'ticket': handoff['issue'],
                  'stage': handoff['stage'], 'adaptation': handoff['adaptation'],
                  'stage_rules': handoff['stage_rules'], 'support_policy': handoff['support_policy'],
                  'entry_points': handoff['skill_entry_points'], 'instructions': instructions}))
    result = agent.run_conversation(prompt, conversation_history=[], task_id=run_id)
    Path('/scratch/conversation.json').write_text(json.dumps(result, default=str))
    final = json.loads(result['final_response'])
    correlated = {**final, 'assignment_id': assignment['assignment_id'], 'handoff_digest': assignment['handoff_digest'],
                  'claim_id': assignment['claim_id'], 'run_id': run_id, 'loads': loads}
    Path('/scratch/result.json').write_text(json.dumps(correlated))
    relay.shutdown()
    events.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        Path('/scratch/worker-error.json').write_text(json.dumps({'error': 'worker_failed', 'type': type(error).__name__, 'message': str(error)[:500]}))
        raise
