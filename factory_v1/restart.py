"""Read-only restart reconciliation at existing dispatch and explicit resume seams.

External effects are adopted only after the existing exact readback validators
accept them. An absent or partial effect is a hold, never permission to resend.
"""
import hashlib
import json
import os
import stat
import sqlite3
from contextlib import closing
from pathlib import Path

from . import assignments, attention, controls, delivery, merge, publication, repositories, tickets
from .errors import IntakeError
from .planning import require, text
from .repositories import RepositoryError
from .sandbox_guard import process_identity, same_process
from .sandbox_policy import Docker
from .sandbox_source import physical


class Readback(assignments.ReadOnlyGitHub):
    def read(self, suffix):
        return self.request('/repos/' + self.repository + suffix)

    def __init__(self, github, repository):
        super().__init__(github)
        self.repository = repository


def scoped(database, project, iteration):
    return [a for a in assignments.rows(database) if
            (a['ticket_scope']['project'], a['ticket_scope']['iteration']) == (project, iteration)]


def require_reconciled(database, project, iteration):
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (project, iteration)).fetchone()
    if row:
        record = json.loads(row[0]).get('restart_reconciliation')
        require(record is None or (isinstance(record, dict) and
                set(record) == {'status', 'observations', 'blockers'} and
                record['status'] in {'ready', 'blocked'} and isinstance(record['observations'], dict) and
                isinstance(record['blockers'], list) and (record['status'] == 'ready') == (not record['blockers'])),
                'Malformed restart evidence cannot grant admission.', 'invalid_restart')
        require(record is None or record.get('status') == 'ready',
                'Restart observations remain unresolved; inspect/reconcile the exact scope.', 'reconciliation_required')
        if database.execute('PRAGMA database_list').fetchone()[2]:
            for assignment in scoped(database, project, iteration):
                runtime = assignment.get('runtime', {})
                if runtime.get('status') in {'preparing', 'running', 'unconfirmed'}:
                    require(type(runtime.get('controller_pid')) is int and text(runtime.get('controller_start')) and
                            same_process(process_identity(runtime['controller_pid']), runtime['controller_start']),
                            'Lost attempt requires exact restart reconciliation before new effects.', 'reconciliation_required')


def worker(assignment):
    runtime = assignment.get('runtime')
    if runtime is None:
        return {'status': 'unlaunched'}
    require(assignments.identifier(runtime.get('run_id')) and assignments.identifier(runtime.get('container')) and
            type(runtime.get('controller_pid')) is int and runtime['controller_pid'] > 0 and
            text(runtime.get('controller_start')) and assignments.absolute(runtime.get('artifacts')),
            'Exact retained attempt ownership required.', 'invalid_restart')
    root = physical(runtime['artifacts'], directory=True)
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            'Private controller-owned artifact envelope required.', 'worker_uncertain')
    live = same_process(process_identity(runtime['controller_pid']), runtime['controller_start'])
    observed = Docker().inspect(runtime['container'], optional=True)
    if observed is not None:
        require(isinstance(observed, dict) and observed.get('Name') == '/' + runtime['container'] and
                type(observed.get('State', {}).get('Running')) is bool,
                'Exact container liveness is unknown or contradictory.', 'worker_uncertain')
    absent = observed is None
    require(not (live and absent and runtime.get('status') == 'running'),
            'Running controller and absent worker disagree.', 'worker_uncertain')
    if not live:
        require(absent, 'Lost controller still has a worker; use exact assignment cleanup first.', 'worker_uncertain')
        if runtime.get('status') not in {'complete', 'failed', 'stopped'} or runtime.get('container_removed') is not True:
            runtime.update(status='stopped', error='controller_lost', container_removed=True, recoverable=True)
    elif runtime.get('status') not in {'preparing', 'running'}:
        require(absent, 'Terminal attempt still has a container.', 'worker_uncertain')
    return {'run_id': runtime['run_id'], 'controller_live': live, 'container_absent': absent,
            'status': runtime['status'], 'artifacts': str(root), 'recoverable': runtime.get('recoverable', False)}


def workspace(assignment, api):
    """Read physical worktree HEAD/refs without executing its Git config or hooks."""
    handoff = assignment['handoff']
    root = physical(handoff['workspace'], directory=True)
    marker = root / '.git'
    if marker.is_dir():
        git = physical(marker, directory=True)
    else:
        line = physical(marker).read_text()
        require(line.startswith('gitdir: ') and len(line.splitlines()) == 1,
                'Unknown worktree marker.', 'workspace_uncertain')
        git = physical(Path(os.path.abspath(root / line.strip()[8:])), directory=True)
    head = physical(git / 'HEAD').read_text().strip()
    branch = None
    if head.startswith('ref: '):
        branch = head[5:]
        require(branch.startswith('refs/heads/') and all(p not in {'', '.', '..'} for p in branch.split('/')),
                'Unsafe workspace ref.', 'workspace_uncertain')
        common = git
        if (git / 'commondir').exists():
            value = physical(git / 'commondir').read_text().strip()
            require(value and '\n' not in value, 'Unknown common Git directory.', 'workspace_uncertain')
            common = physical(Path(os.path.abspath(git / value)), directory=True)
        ref = common / branch
        if ref.exists():
            head = physical(ref).read_text().strip()
        else:
            packed = physical(common / 'packed-refs').read_text().splitlines()
            values = [line.split()[0] for line in packed if line.endswith(' ' + branch)]
            require(len(values) == 1, 'Missing or ambiguous retained branch.', 'workspace_uncertain')
            head = values[0]
    require(assignments.commit(head) and head in {handoff['baseline'], handoff['candidate']},
            'Workspace HEAD drifted from admitted source; retain artifacts and revise assignment.', 'workspace_uncertain')
    for pin in {handoff['baseline'], handoff['candidate']} - {None}:
        value = api.read('/git/commits/' + pin)
        require(isinstance(value, dict) and value.get('sha') == pin,
                'Pinned source commit unavailable.', 'workspace_uncertain')
    return {'path': str(root), 'git_directory': str(git), 'branch': branch, 'head': head}


def candidate(assignment, api):
    journal = assignment.get('publication')
    if journal is None:
        return None
    require(isinstance(journal, dict) and journal.get('status') in {'pending', 'published'},
            'Unknown publication journal.', 'publication_conflict')
    config = journal['approval']
    require(all(publication.valid_ref(config.get(k)) for k in ('branch', 'base')) and
            config['branch'] != config['base'] and config.get('issue') == assignment['handoff']['issue']['number'] and
            assignments.commit(config.get('expected_head')) and assignments.commit(config.get('candidate')),
            'Malformed fixed publication target.', 'publication_conflict')
    require(config.get('binding') == publication.binding(assignment) and
            config.get('repository') == assignment['handoff']['repository'] and
            config.get('repository_id') == assignment['handoff']['repository_id'] and
            config.get('api_base') == api.base and config.get('candidate') == journal['candidate'],
            'Publication authority or target changed.', 'publication_conflict')
    root = physical(assignment['runtime']['artifacts'], directory=True)
    plan, _ = publication.assemble(api, config, assignment, root)
    require(plan['candidate'] == journal['candidate'] and
            publication.ref_sha(api.read('/git/ref/heads/' + config['branch']), config['branch']) == plan['candidate'],
            'Publication is absent, partial or contradictory; never replay writes.', 'publication_uncertain')
    publication.verify_commit(api, plan, config)
    # A merge intent owns the PR after publication; its validator accepts closed PRs.
    if assignment.get('merge') is not None:
        return {'candidate': plan['candidate'], 'tree': plan['tree'], 'pr_number': journal['pr_number']}
    prs = api.read(publication.pr_query(config))
    require(isinstance(prs, list) and len(prs) == 1 and type(prs[0].get('number')) is int and
            prs[0]['number'] > 0 and journal.get('pr_number') in {None, prs[0]['number']},
            'Exact owned PR effect not proven.', 'publication_uncertain')
    number = prs[0]['number']
    title = 'Factory candidate for #' + str(config['issue'])
    body = ('Refs #' + str(config['issue']) + '\n\nCandidate publication only; not delivery, closure or merge.\n\n'
            '<!-- factory-v1-publication:' + assignment['assignment_id'] + ' -->')
    def verify(value):
        require(isinstance(value, dict) and type(value.get('draft')) is bool,
                'Unknown PR readiness.', 'publication_uncertain')
        # Ready-for-review is a later external action, not publication drift.
        # It never supplies independent reviews, checks or merge approval.
        publication.verify_pr(dict(value, draft=True) if journal['status'] == 'published' else value,
                              config, plan, title, body)
    verify(prs[0])
    verify(api.read('/pulls/' + str(number)))
    linked = api.read('/issues/' + str(number))
    require(isinstance(linked, dict) and linked.get('number') == number and linked.get('body') == body and
            linked.get('state') == 'open' and 'pull_request' in linked and
            linked.get('repository_url') == 'https://api.github.com/repos/' + config['repository'],
            'PR issue association not proven.', 'publication_uncertain')
    journal.update(status='published', pr_number=number)
    return {'candidate': plan['candidate'], 'tree': plan['tree'], 'pr_number': number}


def integration(assignment, api):
    journal = assignment.get('merge')
    if journal is None:
        return None
    require(isinstance(journal, dict) and journal.get('status') in
            {'pending', 'merged', 'incomplete', 'delivered', 'revalidation-required'},
            'Unknown merge journal state.', 'merge_conflict')
    config = journal['authority']
    require(all(publication.valid_ref(config.get(k)) for k in ('branch', 'base')) and
            config['branch'] != config['base'] and type(config.get('pr_number')) is int and config['pr_number'] > 0 and
            config.get('issue') == assignment['handoff']['issue']['number'] and
            assignments.commit(config.get('head')) and assignments.commit(config.get('baseline')),
            'Malformed fixed merge target.', 'merge_conflict')
    require(config.get('candidate_assignment') == assignment['assignment_id'] and
            config.get('repository') == assignment['handoff']['repository'] and
            config.get('repository_id') == assignment['handoff']['repository_id'] and config.get('api_base') == api.base,
            'Merge journal scope changed.', 'merge_conflict')
    root = physical(assignment['runtime']['artifacts'], directory=True)
    plan = merge.mapped_candidate(api, config, assignment, root)
    pr = merge.target(api, config)
    evidence = merge.merged_evidence(api, config, pr, plan)
    old = journal.get('integrated_evidence')
    require(old is None or old['integrated'] == evidence['integrated'], 'Integration identity changed.', 'integration_mismatch')
    # Retain a proven effect even when checks are unknown. This is NOT delivery approval.
    journal['integrated_evidence'] = evidence
    if journal['status'] not in {'delivered', 'revalidation-required'}:
        journal['close_allowed'] = False
        journal['status'] = 'merged'
    gate = merge.protection(api, config)
    branch_checks = merge.checks(api, config, config['head'])
    checks = merge.checks(api, config, evidence['integrated'])
    main_checks = merge.checks(api, config, evidence['main'])
    return {'integration': evidence, 'protection': gate, 'branch_checks': branch_checks, 'checks': checks, 'main_checks': main_checks,
            'issue': merge.issue(api, assignment, config)}


def tracker(database, item, api):
    """Reuse idempotent publication/frontier on a disposable read-only-effects copy."""
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        observed = json.loads(json.dumps(item))
        observed['status'] = 'active'
        observed.pop('restart_reconciliation', None)
        repositories.save_iteration(observation, observed)
        observation.commit()
        repositories.reconcile(observation, observed, api)
        from . import bugs
        for identity, work in list(observed.get('bug_work', {}).items()):
            if work.get('bug') and work.get('execution') and not work.get('requirement_disposition'):
                observed = bugs.publish(observation, observed, {'assignment_id': identity}, api)
                require(observed['bug_work'][identity].get('publication_complete'),
                        'Bug effects remain partial; use scoped reconciliation, never replay writes.', 'publication_uncertain')
        for key in ('bug_work', 'requirement_authority'):
            if key in observed:
                item[key] = observed[key]
        if observed.get('ticket_work') is not None:
            work = observed['ticket_work']
            if not work.get('publication_complete') and work.get('tickets'):
                observed = tickets.publish(observation, observed, api)
                require(observed['ticket_work'].get('publication_complete'),
                        'Ticket effects remain partial; no replay permitted.', 'publication_uncertain')
            else:
                observed = tickets.frontier(observation, observed, api)
            frontier = observed['ticket_work']['frontier']
            require(not frontier['foreign_active'] and len(frontier['active']) <= 1,
                    'Contradicting authoritative active tickets.', 'claim_conflict')
            reservation = observed.get('reservation')
            if reservation:
                require(reservation.get('assignment_id') == work['assignment_id'] and
                        reservation.get('input_digest') == work['input_digest'] and
                        reservation.get('status') in {'pending', 'reserved'},
                        'Stale reservation authority.', 'reservation_conflict')
                row = next((r for r in frontier['items'] if r['number'] == reservation['issue_number']), None)
                require(row is not None and row['admissible'] and set(frontier['active']) <= {row['number']},
                        'Reservation is no longer in exact eligible scope.', 'reservation_conflict')
                assert row is not None
                if row['status'] == 'active':
                    reservation['status'] = 'reserved'
                else:
                    require(reservation['status'] != 'pending', 'Reservation status effect is unknown.', 'reservation_conflict')
            item['ticket_work'] = observed['ticket_work']
            if reservation:
                item['reservation'] = reservation
        elif item.get('handoff'):
            tickets.pinned_inputs(observation, observed, api)
        item['repository_onboarding'] = observed['repository_onboarding']
        return item.get('ticket_work', {}).get('frontier')


def ownership(database, item):
    """Check both registries, including other scopes, without stealing claims."""
    from .requirements import released_claim as revised
    from .diagnosis import released_claim as directed
    active = [a for a in assignments.rows(database) if not revised(database, a) and not directed(database, a) and
              not released_claim(a) and not assignments.completed_iteration_claim(database, a)]
    profiles, homes, ticket_scopes = set(), set(), set()
    for a in active:
        p = a['handoff']['profile']
        require(p['name'] not in profiles and p['home'] not in homes,
                'Concurrent role-profile ownership.', 'profile_claim_conflict')
        profiles.add(p['name'])
        homes.add(p['home'])
        if a['handoff']['stage'] != 'diagnosis':
            s = a['ticket_scope']
            ticket_scopes.add((s['project'], s['iteration'], s['repository'], s['repository_id'], s['issue_number']))
    for row in database.execute('SELECT payload FROM iterations'):
        other = json.loads(row[0])
        if other.get('reservation'):
            ticket_scopes.add((other['project_id'], other['iteration_id'], other['repository'],
                              other['repository_onboarding']['metadata']['id'], other['reservation']['issue_number']))
    require(len(ticket_scopes) <= 1, 'Conflicting ticket/role ownership.', 'claim_conflict')
    return {'assignments': [a['assignment_id'] for a in active], 'profiles': sorted(profiles)}


def released_claim(assignment):
    disposition = assignment.get('restart_disposition')
    if disposition is None:
        return False
    runtime = assignment.get('runtime', {})
    require(isinstance(disposition, dict) and set(disposition) == {'run_id', 'reference', 'operator_id'} and
            disposition['run_id'] == runtime.get('run_id') and text(disposition['reference']) and
            text(disposition['operator_id']) and runtime.get('recoverable') is True and
            runtime.get('container_removed') is True and runtime.get('status') in {'stopped', 'failed'},
            'Invalid recovered claim; no ownership release permitted.', 'invalid_restart')
    return True


def reconcile(database, project, iteration, operator, github, *, recover=None):
    """Caller holds the existing state lock; remote capability is read-only here."""
    require(bool(database.execute('PRAGMA database_list').fetchone()[2]),
            'Restart reconciliation requires persistent controller state.', 'invalid_restart')
    controls.owner(database, project, iteration, operator)
    item = assignments.current(database, project, iteration, operator, require_active=False)
    require(repositories.valid_repository(item.get('repository')),
            'Exact registered repository grammar required.', 'invalid_restart')
    api = Readback(github, item['repository'])
    report = {'status': 'blocked', 'observations': {}, 'blockers': []}
    item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                                      'blockers': [{'boundary': 'restart', 'code': 'reconciliation_interrupted'}]}
    repositories.save_iteration(database, item)
    database.commit()  # Crash anywhere during observation cannot leave a ready receipt.
    observations, blockers = report['observations'], report['blockers']

    def observe(key, operation):
        try:
            observations[key] = operation()
            return True
        except (RepositoryError, IntakeError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            blockers.append({'boundary': key, 'code': error.code if isinstance(error, (RepositoryError, IntakeError)) else 'invalid_restart'})
            return False

    observe('tracker', lambda: tracker(database, item, api))
    requirement_reads = []
    retained = scoped(database, project, iteration)
    for a in retained:
        identity = a['assignment_id']
        def scope(a=a):
            s, h = a['ticket_scope'], a['handoff']
            require(s['repository'] == h['repository'] == item['repository'] and
                    s['repository_id'] == h['repository_id'] == item['repository_onboarding']['metadata']['id'] and
                    s['issue_number'] == h['issue']['number'] and s['issue_id'] == h['issue']['id'],
                    'Retained ownership does not bind this exact repository/issue.', 'scope_mismatch')
            for journal, authority in (('publication', 'approval'), ('merge', 'authority')):
                if a.get(journal):
                    require(a[journal][authority].get('operator_id') == operator,
                            'Retained effect authority belongs to another operator.', 'approval_required')
            return s
        if not observe(identity + ':scope', scope):
            continue
        observe(identity + ':worker', lambda a=a: worker(a))
        # Never promote a worker's claimed tests, results or completion.
        observe(identity + ':workspace', lambda a=a: workspace(a, api))
        if not a.get('requirement_disposition') and not released_claim(a):
            def requirements(a=a):
                from .requirements import authority, revalidate_inputs
                require(a['revision'] == authority(item, a['ticket_scope']['issue_number']) and
                        assignments.digest(a['handoff']) == a['handoff_digest'],
                        'Retained assignment authority changed.', 'stale_assignment')
                revalidate_inputs(database, item, api, a['ticket_scope']['issue_number'])
                issue = tickets.gh.read_issue(api, item['repository'], a['handoff']['issue']['number'])
                require(all(issue[k] == a['handoff']['issue'][k] for k in ('number', 'id', 'node_id')) and
                        hashlib.sha256(issue['body'].encode()).hexdigest() == a['handoff']['issue']['body_sha256'],
                        'Exact assignment issue changed.', 'scope_mismatch')
                return {'revision': a['revision'], 'issue': a['handoff']['issue']}
            observe(identity + ':requirements', requirements)
            requirement_reads.append((identity, requirements))
        observe(identity + ':publication', lambda a=a: candidate(a, api))
        observe(identity + ':merge', lambda a=a: integration(a, api))
        # Truthful known-effect/absence receipts remain retainable while paused.
        publication.save(database, a)
    def notifications():
        records = attention.records(database, project, iteration, item)
        require(not any(r['state'] == 'uncertain' for r in records),
                'Unknown attention effects require exact trusted receipt; never resend.', 'attention_uncertain')
        return records
    observe('attention', notifications)
    checkpoint = controls.snapshot(database, project, iteration)
    if checkpoint['pending_model_operations'] or checkpoint['brokers']:
        blockers.append({'boundary': 'model', 'code': 'operation_uncertain'})
    # Auxiliary publication/revision/debug journals retain their existing dedicated
    # scoped reconcilers; unresolved effects must not become dispatch permission.
    for work in [*item.get('bug_work', {}).values(), *item.get('bug_work_history', [])]:
        if any(work.get(key) for key in ('issue_intent', 'membership_intent', 'edge_intents', 'affected_intents', 'field_intents')) and not work.get('publication_complete'):
            blockers.append({'boundary': 'bug-publication', 'code': 'publication_uncertain'})
    if item.get('delivery_monitor') is not None:
        def health():
            result = delivery.sample(database, project, iteration, operator)
            require(result['evidence']['status'] == 'healthy', 'Configured health is failed/unknown.', 'main_frozen')
            return result
        observe('delivery', health)
        # sample owns its incident/outbox pointers; never overwrite them with our snapshot.
        latest = assignments.current(database, project, iteration, operator, require_active=False)
        for key in ('delivery_monitor', 'main_incident'):
            if key in latest:
                item[key] = latest[key]
            else:
                item.pop(key, None)
    # Health/CI and publication reads can expose concurrent requirement/board
    # edits. Refresh the authoritative frontier and every retained input last.
    if not blockers:
        initial = observations.get('tracker')
        def final_tracker():
            fresh = tracker(database, item, api)
            require(fresh == initial, 'Authoritative frontier changed during restart observations.', 'stale_admission')
            return fresh
        observe('final-tracker', final_tracker)
        for identity, operation in requirement_reads:
            observe(identity + ':final-requirements', operation)
    observe('ownership', lambda: ownership(database, item))
    if recover is not None:
        identity, run_id, reference = recover
        target = next((a for a in retained if a['assignment_id'] == identity), None)
        def release():
            require(not blockers and target is not None and text(reference) and len(reference) <= 500,
                    'Complete fresh readback and explicit exact recovery direction required.', 'reconciliation_required')
            assert target is not None
            runtime = target.get('runtime', {})
            require(runtime.get('run_id') == run_id and runtime.get('recoverable') is True and
                    runtime.get('container_removed') is True and runtime.get('status') in {'stopped', 'failed'} and
                    not same_process(process_identity(runtime['controller_pid']), runtime['controller_start']) and
                    not target.get('publication') and not target.get('merge'),
                    'Wrong/live/completed/published attempt cannot release ownership.', 'run_conflict')
            with controls.lane(database, project, iteration):
                target['restart_disposition'] = {'run_id': run_id, 'reference': reference, 'operator_id': operator}
                publication.save(database, target)
            return {'assignment_id': identity, 'run_id': run_id}
        observe('recovery', release)
        observe('final-ownership', lambda: ownership(database, item))
    report['status'] = 'blocked' if blockers else 'ready'
    item['restart_reconciliation'] = report
    repositories.save_iteration(database, item)
    database.commit()
    return {**report, 'project': project, 'iteration': iteration,
            'dispatch_allowed': not blockers and item['status'] == 'active' and not controls.blocked(database, project, iteration)}


def before_dispatch(database, project, iteration, operator, github, *, force=False):
    item = assignments.current(database, project, iteration, operator, require_active=False)
    # Initial admission already reads the exact tracker/requirements at its own
    # seam. Broader restart inventory is needed once an attempt or observer exists.
    if not force and not item.get('restart_reconciliation') and not item.get('delivery_monitor') and not any(
            a.get('runtime') for a in scoped(database, project, iteration)):
        controls.require_active(database, project, iteration)
        return None
    report = reconcile(database, project, iteration, operator, github)
    require(report['status'] == 'ready', 'Exact restart observations prevent dispatch; inspect reconciliation evidence.', 'reconciliation_required')
    controls.require_active(database, project, iteration)
    return report
