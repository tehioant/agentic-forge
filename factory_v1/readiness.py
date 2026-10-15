"""Read-only capability inventory. Reports are observations, never dispatch authority.

Use collect() from trusted host Python. Independent capability readers must validate
actual scoped authority, not caller booleans or future worker completion evidence.
The built-in readers deliberately refuse unsupported authority introspection.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import time

from . import assignments, controls, delivery, merge, messaging, planning, repositories
from . import requirement_execution, role_skills, sandbox_policy, service_dispatch, skills, spending, tickets
from .model_access import SUBSCRIPTION_SCOPE, SubscriptionTransport
from .origin import validate_origin
from .planning import require

FIELDS = {'operator_id', 'project', 'iteration', 'reference', 'slots', 'planning_skills',
          'ticket_skill', 'board', 'board_pin', 'protection', 'synthesis_config'}
WRITES = ('issues', 'projects', 'contents', 'pull_requests', 'merge')


class UnsupportedReaders:
    """No existing adapter proves these general infrastructure capabilities.

    Host integrations may supply read-only validators here. Return a SHA-256 of
    exact validated authority inputs; raise on absent, insufficient or stale
    authority. These methods are trusted code, never configured user values.
    """
    def github_authority(self, scope, operations):
        raise repositories.RepositoryError('unsupported', 'No scoped write-authority reader installed.')

    def execution_support(self, scope, stages):
        raise repositories.RepositoryError('unsupported', 'No independent executor/verifier capacity reader installed.')


class ReadAPI:
    def __init__(self, github, repository):
        self.github = github
        self.prefix = '/repos/' + repository

    def read(self, suffix):
        return self.github.request(self.prefix + suffix)


def load_config(path):
    """Use the existing private, controller-owned configuration convention."""
    return json.loads(merge.private_file(path).read_text(),
                      object_pairs_hook=repositories.unambiguous_fields)


def collect(database, service, config, github, *, readers=None, running=None, now=None):
    """Inspect exact registered inputs without claims, reservations, execution or sends.

    running is a separately observed liveness fact, not readiness or permission.
    dispatch_allowed stays false until boundary wiring is implemented. Recollect
    and compare input_identity at sensitive boundaries; never reuse a green report.
    """
    service, config = deepcopy(service), deepcopy(config)
    readers = readers or UnsupportedReaders()
    now = int(time.time()) if now is None else now
    results, pins = {}, {}

    def check(name, remediation, action):
        try:
            value = action()
            pins[name] = assignments.digest(value)
            results[name] = {'state': 'ready', 'code': 'verified', 'remediation': None}
            return value
        except Exception:
            # Neither arbitrary exceptions nor adapter-supplied error codes are safe.
            results[name] = {'state': 'blocked', 'code': name + '_blocked', 'remediation': remediation}
            return None

    def scope_check():
        require(isinstance(config, dict) and set(config) == FIELDS and
                all(assignments.identifier(config[k]) and config[k] == service[k]
                    for k in ('operator_id', 'project', 'iteration')) and planning.text(config['reference']),
                'Exact scoped configuration required.', 'invalid_readiness')
        row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                               (config['project'], config['iteration'])).fetchone()
        require(row is not None, 'Registered iteration required.', 'not_found')
        item = json.loads(row[0])
        require(item['project_id'] == config['project'] and item['iteration_id'] == config['iteration'] and
                item['approval']['operator_id'] == config['operator_id'] and planning.text(item['approval']['reference']) and
                item.get('execution_allowed') is False, 'Exact approval required.', 'approval_required')
        require(delivery.valid_repository(item['repository']) and
                type(item['repository_onboarding']['metadata']['id']) is int and
                item['repository_onboarding']['metadata']['id'] > 0 and
                item['repository_onboarding']['api_base'] == service['api_base'] == github.base,
                'Pin exact repository and capability endpoint.', 'scope_mismatch')
        return item

    item = check('scope', 'Select the registered operator/project/iteration and immutable repository endpoint.', scope_check)
    if item is None:
        return _report(results, pins, None, now, running)
    scope = {'project': config['project'], 'iteration': config['iteration'], 'operator_id': config['operator_id'],
             'repository': item['repository'], 'repository_id': item['repository_onboarding']['metadata']['id'],
             'api_base': github.base, 'board': config['board_pin']}
    readonly = assignments.ReadOnlyGitHub(github)

    def repository():
        metadata = repositories.validate_metadata(readonly.request('/repos/' + scope['repository']), scope['repository'])
        require(metadata['id'] == scope['repository_id'] and metadata['private'] is True,
                'Private immutable target required.', 'repository_mismatch')
        return {'id': metadata['id'], 'private': True, 'repository': metadata['full_name']}
    check('repository', 'Restore access to the exact approved private repository; do not create or adopt replacements.', repository)

    def board():
        require(planning.text(config['board']['project_id']), 'Explicit board required.', 'projects_blocked')
        observed = tickets.gh.resolve_board(readonly, scope['repository'], config['board'])
        require(observed == config['board_pin'], 'Board identity/schema changed.', 'projects_blocked')
        return observed
    check('board', 'Restore exact linked Projects access and pinned project/field/option IDs.', board)

    def authority():
        pin = readers.github_authority(deepcopy(scope), WRITES)
        require(assignments.sha(pin), 'Independent exact write authority unavailable.', 'capability_blocked')
        return pin
    check('github_authority', 'Install a trusted scoped Issues/Projects/contents/PR/merge authority reader; successful reads do not prove writes.', authority)

    def stage_inputs():
        slots = config['slots']
        require(isinstance(slots, list) and 0 < len(slots) <= 1000 and
                {s['stage'] for s in slots} == set(assignments.ROLES), 'Inventory every configured engineering stage.', 'invalid_dispatch')
        names, homes, claims = set(), set(), set()
        sources = {}
        existing = assignments.rows(database)
        for slot in slots:
            require(set(slot) == {'stage', 'profile', 'skills', 'claim_id', 'launcher'} and
                    assignments.identifier(slot['claim_id']), 'Explicit finite slots required.', 'invalid_dispatch')
            profile = assignments.validate_profile(slot['stage'], slot['profile'])
            require(profile['name'] not in names and profile['home'] not in homes and slot['claim_id'] not in claims and
                    not Path(profile['home']).exists() and not Path(profile['home']).is_symlink(),
                    'Fresh distinct profiles required.', 'claim_conflict')
            require(not any(r['claim_id'] == slot['claim_id'] or r['handoff']['profile']['name'] == profile['name'] or
                            r['handoff']['profile']['home'] == profile['home'] for r in existing),
                    'Slot already consumed.', 'claim_conflict')
            names.add(profile['name']); homes.add(profile['home']); claims.add(slot['claim_id'])
            sources[slot['claim_id']] = service_dispatch.installed(slot['stage'], slot['skills'])
        sources['planning'] = skills.resolve_installed(config['planning_skills'])
        descriptor = config['ticket_skill']
        source = skills.INSTALLED_ROOT / 'to-tickets/SKILL.md'
        require(descriptor['path'] == str(source) and not any(p.is_symlink() for p in (source, *source.parents)),
                'Use installed ticket source.', 'skill_blocked')
        sources['tickets'] = tickets.skill(descriptor)
        return {'sources': sources, 'adaptations': role_skills.ADAPTATIONS,
                'planning_adaptation': planning.INSTRUCTIONS, 'tickets_adaptation': tickets.INSTRUCTIONS}
    stages = check('stages', 'Review installed source/dependency/adaptation drift and provide unused distinct finite stage slots; never repin automatically.', stage_inputs)

    def containers():
        configurations = []
        for slot in config['slots']:
            launcher = sandbox_policy.load_config(slot['launcher'])
            SubscriptionTransport(launcher['subscription_socket'], (config['project'], config['iteration'], *SUBSCRIPTION_SCOPE), 10)
            configurations.append(launcher)
        image = sandbox_policy.Docker().image()
        return {'launchers': configurations, 'image': image, 'policy_sha256': hashlib.sha256(Path(sandbox_policy.__file__).read_bytes()).hexdigest()}
    check('containers', 'Restore reviewed image, private launcher limits/artifact paths and owned fixed subscription socket; no container is started by inspection.', containers)

    check('spending', 'Restore unexpired exact approved subscription allowance or reconcile quota/uncertain operation; no new grant or fallback.',
          lambda: spending.admission_preview(database, (config['project'], config['iteration'], *SUBSCRIPTION_SCOPE), now=now))

    def execution():
        require(stages is not None, 'Installed sources required.', 'skill_blocked')
        synthesis = requirement_execution.configuration(database, config['synthesis_config'], config['operator_id'])
        pin = readers.execution_support(deepcopy(scope), tuple(['grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES]))
        require(assignments.sha(pin), 'Independent capacity/verifier support required.', 'execution_required')
        return {'synthesis': synthesis, 'support': pin}
    check('execution', 'Verify native planning, host synthesis, engineering executors and independent evidence verifiers without running skills.', execution)

    def health():
        contract = delivery.load_config(service['delivery_config'], config['operator_id'])
        require(all(contract[k] == scope[k] for k in ('repository', 'repository_id', 'api_base')),
                'Delivery scope mismatch.', 'delivery_scope')
        protection = config['protection']
        require(protection['base'] == 'main' and protection['required_checks'] == contract['required_checks'],
                'Exact main check contract required.', 'checks_required')
        protected = merge.protection(ReadAPI(readonly, scope['repository']), protection)
        observation = delivery.observe(contract, ReadAPI(readonly, scope['repository']))
        require(observation['status'] == 'healthy', 'Configured health unknown or failed.', 'delivery_unknown')
        return {'config': contract, 'protection': protected, 'observation': observation}
    check('delivery', 'Restore exact main protection/check/workflow/deployment-health contract; no-deployment contracts are currently unsupported.', health)

    def notification():
        validate_origin(item['origin'])
        transport = messaging.HermesTransport(service['transport_config'], item['origin'])
        executable = sandbox_policy.physical(transport.command[0])
        info = executable.stat()
        require(len(transport.command) == 1 and info.st_uid == os.getuid() and
                not info.st_mode & 0o022 and os.access(executable, os.X_OK),
                'Exact executable route required.', 'capability_required')
        return {'origin': item['origin'], 'command_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}
    check('notification', 'Configure the exact originating thread and pinned host executable; this checks routing, not delivery or platform allowance.', notification)
    eligible = check('controls', 'Explicitly resume and reconcile existing pause/stop/iteration holds; cleanup remains available.',
                     lambda: _controls(database, item))
    pins['configuration'] = assignments.digest({'service': service, 'readiness': config})
    return _report(results, pins, eligible, now, running)


def _controls(database, item):
    require(item['status'] == 'active' and not controls.blocked(database, item['project_id'], item['iteration_id']),
            'Iteration held.', 'iteration_paused')
    return {'eligible': True}


def _report(results, pins, eligible, now, running):
    capabilities = {k: v for k, v in results.items() if k != 'controls'}
    return {'version': 1, 'observed_at': now, 'service_running': running if type(running) is bool else None,
            'capability_ready': bool(capabilities) and all(r['state'] == 'ready' for r in capabilities.values()),
            'iteration_eligible': eligible is not None, 'iteration_runnable': False, 'dispatch_allowed': False,
            'boundary_wiring': 'not_implemented', 'retained_report_is_authority': False,
            'input_identity': assignments.digest(pins), 'checks': results}
