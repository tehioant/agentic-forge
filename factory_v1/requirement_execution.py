"""Invoke a separately approved host synthesis capability, never accept supplied artifacts."""
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from . import planning, requirements, tickets
from .planning import require, text
from .publication import read_json
from .repositories import RepositoryError
from .sandbox_source import physical
from .skills import resolve


def configuration(database, path, operator):
    from .merge import controller_paths
    controller_paths(database, path)
    file = physical(path)
    info = file.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
            'Private host-only synthesis capability required.', 'approval_required')
    config = read_json(file)
    require(isinstance(config, dict) and set(config) == {'operator_id', 'reference', 'execution_reference', 'argv', 'sha256', 'timeout'} and
            config['operator_id'] == operator and text(config['reference']) and text(config['execution_reference']) and
            isinstance(config['argv'], list) and config['argv'] and all(text(a) for a in config['argv']) and
            type(config['timeout']) is int and 1 <= config['timeout'] <= 300,
            'Authorize a bounded exact host executor and independent execution provenance.', 'approval_required')
    executable = physical(config['argv'][0])
    info = executable.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022 and os.access(executable, os.X_OK) and
            hashlib.sha256(executable.read_bytes()).hexdigest() == config['sha256'],
            'Selected host executor bytes or ownership changed.', 'execution_required')
    from .assignments import rows
    for assignment in rows(database):
        scopes = [assignment['handoff']['workspace'], assignment['handoff']['profile']['home']]
        if assignment.get('runtime'):
            scopes.append(assignment['runtime']['artifacts'])
        require(not any(executable.is_relative_to(Path(scope)) for scope in scopes),
                'Host executor must be outside worker mounts.', 'execution_required')
    require(len(config['argv']) == 1, 'Use one pinned executable adapter, not unpinned script arguments.', 'execution_required')
    return config


def invoke(config, payload, *, cancelled=None):
    """The admitted adapter owns actual conversational execution/approved model access."""
    require(cancelled is None or not cancelled(), 'Interrupted before host execution.', 'iteration_paused')
    pending = json.dumps(payload, sort_keys=True).encode()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(config['argv'], stdin=subprocess.PIPE, stdout=output, stderr=errors,
                                   start_new_session=True, env={'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8'})
        deadline = time.monotonic() + config['timeout']
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (cancelled is not None and cancelled()):
                    raise subprocess.TimeoutExpired(config['argv'][0], config['timeout'])
                try:
                    process.communicate(pending, timeout=min(0.1, remaining) if cancelled is not None else remaining)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            finally:
                process.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RepositoryError('execution_uncertain', 'Synthesis interrupted/timed out; reconcile the admitted attempt, never automatically retry.') from error
            raise
        require(process.returncode == 0, 'Host synthesis failed; retain the unresolved attempt without fallback.', 'execution_uncertain')
        output.seek(0)
        raw = output.read(8 * 1024 * 1024 + 1)
        require(len(raw) <= 8 * 1024 * 1024, 'Bounded synthesis result required.', 'execution_required')
        from .repositories import unambiguous_fields
        value = json.loads(raw, object_pairs_hook=unambiguous_fields)
        require(isinstance(value, dict) and set(value) == {'result', 'execution'}, 'Actual synthesis result and execution required.', 'execution_required')
        return value


def run_stage(database, item, record, stage, config, work):
    if stage in record.get('executions', {}):
        retained = record['executions'][stage]
        require(retained['input_digest'] == tickets.digest(work) and retained['source'] == work['skill'],
                'Retained synthesis inputs changed.', 'stale_requirements')
        tickets.validate_execution(work, retained['evidence'], retained['result'])
        return retained['result']
    require(stage not in record.get('attempts', {}), 'Uncertain synthesis must reconcile; no automatic repeated usage.', 'execution_uncertain')
    record.setdefault('attempts', {})[stage] = {'input_digest': tickets.digest(work), 'capability': config}
    requirements.checkpoint(database, item)
    produced = invoke(config, work)
    record.setdefault('execution_receipts', {})[stage] = produced
    requirements.checkpoint(database, item, receipt=True)
    evidence = tickets.validate_execution(work, produced['execution'], produced['result'])
    record.setdefault('executions', {})[stage] = {'result': produced['result'], 'evidence': evidence,
        'source': work['skill'], 'dependencies': work['skill']['dependencies'],
        'adaptation': requirements.ADAPTATION, 'input_digest': tickets.digest(work), 'work': work,
        'host_execution_reference': config['execution_reference']}
    requirements.checkpoint(database, item)
    return produced['result']


def execute(database, project, iteration, operator, request, github, config_path):
    item, record = requirements.record_for(database, project, iteration, operator, request)
    require(set(request) == {'change_id', 'skills'} and record['status'] in {'resolved', 'synthesized'},
            'Resolve Antoine\'s direction before selected synthesis.', 'direction_required')
    config = configuration(database, config_path, operator)
    selected = resolve(request['skills'])
    # Reuse the installed planning closure; only to-spec executes, not another interview.
    spec = {**selected['to-spec'], 'dependencies': []}
    ticket_skill = tickets.skill(item['ticket_work']['request']['skill'])
    requirements.sync_blocks(database, item, record, github)
    old_inputs = {str(n): requirements.revalidate_inputs(database, item, github, n) for n in record['affected']}
    docs = old_inputs[str(record['affected'][0])]['documents']
    require(all(i['documents'] == docs for i in old_inputs.values()),
            'This bounded change needs one common old synthesis revision.', 'change_conflict')
    source_pins = {d['url']: d['sha256'] for d in docs.values()}
    sources = {**source_pins, 'revision-direction': tickets.digest(record['resolution']),
               'revision-scope': tickets.digest({'issues': record['old_issues'], 'requirements': record['requirements'], 'blockers': record['blockers']}),
               'revision-adaptation': tickets.digest({'adaptation': requirements.ADAPTATION, 'instructions': requirements.INSTRUCTIONS})}
    work = {'assignment_id': request['change_id'], 'stage': 'to-spec', 'skill': spec,
            'inputs': {'documents': docs}, 'load_sources': sources, 'adaptation': requirements.ADAPTATION,
            'instructions': requirements.INSTRUCTIONS, 'origin': item['origin'], 'direction': record['resolution'],
            'affected': record['old_issues'], 'requirements': record['requirements'], 'blockers': record['blockers'],
            'result_contract': 'three-markdown-documents'}
    # The execution validator uses exactly the same load/work contract as ticket synthesis.
    documents = run_stage(database, item, record, 'to-spec', config, work)
    require(isinstance(documents, dict) and set(documents) == {'milestone.md', 'vision.md', 'provenance.md'} and
            all(text(v) for v in documents.values()), 'to-spec must produce the actual three documents.', 'execution_required')
    planning.validate_documents(documents)
    synthesized_docs = {n: {'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                            'url': 'revision-document:' + request['change_id'] + ':' + n} for n, content in documents.items()}
    work = {**work, 'stage': 'to-tickets', 'skill': ticket_skill, 'inputs': {'documents': synthesized_docs},
            'result_contract': 'affected-tickets-with-unchanged-identity-and-blockers'}
    batch = run_stage(database, item, record, 'to-tickets', config, work)
    require(isinstance(batch, list) and len(batch) == len(record['affected']) and
            all(isinstance(t, dict) and set(t) == {'number', 'title', 'desired_behavior', 'references', 'acceptance_criteria', 'blockers'} and
                type(t['number']) is int for t in batch) and {t['number'] for t in batch} == set(record['affected']),
            'Synthesize every affected ticket only, without extra work or invented identities.', 'scope_mismatch')
    for ticket in batch:
        require(ticket['blockers'] == record['blockers'][str(ticket['number'])],
                'Dependency changes require a separately resolved scope; never infer them.', 'scope_mismatch')
        tickets.validate_contract(ticket['title'], ticket['desired_behavior'], ticket['acceptance_criteria'], ticket['references'], work['inputs'])
    require(resolve(request['skills']) == selected and tickets.skill(item['ticket_work']['request']['skill']) == ticket_skill,
            'Selected skill bytes changed during executing-context synthesis.', 'skill_blocked')
    require(configuration(database, config_path, operator) == config, 'Host synthesis capability changed.', 'execution_required')
    record.update(status='synthesized', documents=documents, tickets=batch, synthesis_request=request)
    requirements.checkpoint(database, item)
    return item
