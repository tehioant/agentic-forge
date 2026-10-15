"""Fresh supervised admission over the existing private service/state envelope.

No report, caller token or environment credential grants admission. External reads
finish before the interruption lane; only local comparisons run inside that lane.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path

from . import assignments, controls, readiness
from .planning import require

_active = ContextVar('supervised_readiness_admission', default={})


def binding(database, project, iteration):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE name='supervised_readiness'").fetchone():
        return None
    row = database.execute('SELECT service, policy FROM supervised_readiness WHERE project=? AND iteration=?',
                           (project, iteration)).fetchone()
    return row


def register(database, service_path, config):
    """Persist the service selection, including a missing-readiness fail-closed state."""
    database.execute('CREATE TABLE IF NOT EXISTS supervised_readiness '
                     '(project TEXT, iteration TEXT, service TEXT, policy TEXT, PRIMARY KEY(project, iteration))')
    old = binding(database, config['project'], config['iteration'])
    require(old is None or old[0] == str(service_path), 'Supervised service binding changed.', 'readiness_binding_changed')
    database.execute('INSERT OR IGNORE INTO supervised_readiness VALUES (?,?,?,NULL)',
                     (config['project'], config['iteration'], str(service_path)))
    database.commit()


def configuration(path):
    # Do not call service.load: it registers state and would recurse during admission.
    from . import service
    config = readiness.load_config(path)
    require(isinstance(config, dict) and set(config) == service.FIELDS,
            'Exact service configuration required.', 'invalid_service')
    require(config['readiness_config'] is not None, 'Select private readiness configuration.', 'readiness_configuration_required')
    return config, readiness.load_config(config['readiness_config'])


def policy(config, selected, database):
    """Immutable local admission inputs, not counters, health samples or slot use."""
    files = {}
    visited = set()
    state = config['state']

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.startswith('/') and value != state and value not in visited:
            visited.add(value)
            path = Path(value)
            if path.is_file():
                require(not any(p.is_symlink() for p in (path, *path.parents)),
                        'Physical selected inputs required.', 'readiness_inputs_changed')
                info = path.stat()
                raw = path.read_bytes()
                files[value] = [hashlib.sha256(raw).hexdigest(), info.st_mode, info.st_uid, info.st_ino]
                if path.suffix == '.json':
                    visit(json.loads(raw))
    visit(config)
    visit(selected)
    for path in Path(__file__).parent.glob('*.py'):
        visit(str(path.absolute()))
    grant_columns = [r[1] for r in database.execute('PRAGMA table_info(model_grants)') if r[1] != 'remaining']
    require(grant_columns, 'Existing allowance policy required.', 'readiness_configuration_required')
    grants = database.execute('SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in grant_columns) +
                              ' FROM model_grants WHERE project=? AND iteration=? ORDER BY grant_id',
                              (config['project'], config['iteration'])).fetchall()
    return assignments.digest({'service': config, 'selected': selected, 'files': files,
                               'grant_columns': grant_columns, 'grants': grants})


def seal(database, path):
    """Explicit operator selection pins existing local policy; it grants no capability."""
    config, selected = configuration(path)
    controls.owner(database, config['project'], config['iteration'], config['operator_id'])
    current = binding(database, config['project'], config['iteration'])
    require(current is not None and current[0] == str(path), 'Registered service required.', 'readiness_binding_changed')
    require(controls.blocked(database, config['project'], config['iteration']),
            'Pause before selecting readiness inputs.', 'iteration_paused')
    value = policy(config, selected, database)
    with controls.lane(database, config['project'], config['iteration']):
        require(policy(config, selected, database) == value, 'Inputs changed during selection.', 'readiness_inputs_changed')
        database.execute('UPDATE supervised_readiness SET policy=? WHERE project=? AND iteration=?',
                         (value, config['project'], config['iteration']))
        database.commit()


def snapshot(database, project, iteration):
    # This short-lived comparison intentionally includes mutable state: it detects
    # changes DURING observation, never seals changing observations across calls.
    item = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                            (project, iteration)).fetchone()
    tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = {}
    for name in ('role_assignments', 'model_grants', 'model_operations'):
        if name in tables:
            rows[name] = sorted(database.execute('SELECT * FROM ' + name).fetchall(), key=repr)
    return assignments.digest({'item': item, 'rows': rows, 'binding': binding(database, project, iteration)})


def observe(database, project, iteration, *, resolution=None, selection=None):
    current = binding(database, project, iteration)
    if current is None:
        return None
    from .service import github_client
    config, selected = configuration(current[0])
    require((config['project'], config['iteration']) == (project, iteration) and
            config['state'] == database.execute('PRAGMA database_list').fetchone()[2],
            'Exact supervised state required.', 'readiness_binding_changed')
    before = snapshot(database, project, iteration)
    local = policy(config, selected, database)
    require(current[1] == local, 'Select/review changed local readiness inputs while paused.', 'readiness_inputs_changed')
    report = readiness.collect(database, config, selected, github_client(config))
    excluded = {'controls'}
    if resolution is not None:
        from .effect_resolution import permitted
        if permitted(database, project, iteration, resolution):
            # Exact previously-authorized effect resolution needs no NEW worker
            # allowance, unused slot or healthy deployment. All other gates stay.
            excluded |= {'capacity', 'spending', 'delivery'}
    require(all(v['state'] == 'ready' for k, v in report['checks'].items() if k not in excluded),
            'Current scoped capabilities unavailable; inspect readiness.', 'readiness_blocked')
    validate_selection(database, config, selected, selection)
    return config, selected, local, before


def validate_selection(database, config, selected, selection):
    if selection is None:
        return
    scope = {'project': config['project'], 'iteration': config['iteration']}
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    scope.update(repository=item['repository'], repository_id=item['repository_onboarding']['metadata']['id'])
    if 'assignment_id' in selection:
        row = assignments.inspect(database, config['project'], config['iteration'], config['operator_id'], selection['assignment_id'])
        request = row['handoff']
        require(selection.get('run_id') == (row.get('runtime') or {}).get('run_id'),
                'Selected run changed.', 'slot_binding_changed')
        slot = next((s for s in selected['slots'] if s['claim_id'] == row['claim_id']), None)
    else:
        request = selection
        readiness.slot_capacity(database, selected['slots'], scope, stage=request['stage'])
        slot = next((s for s in selected['slots'] if s['claim_id'] == request['claim_id']), None)
    require(slot is not None and slot['stage'] == request['stage'] and slot['profile'] == request['profile'],
            'Selected stage/profile/claim not configured.', 'slot_binding_changed')
    assert slot is not None
    descriptors = [{k: v for k, v in d.items() if k != 'instructions'} for d in request['skills']]
    require(slot['skills'] == descriptors, 'Selected source closure changed.', 'slot_binding_changed')


def compare(database, project, iteration, observation):
    if observation is None:
        require(binding(database, project, iteration) is None, 'Supervision changed.', 'readiness_binding_changed')
        return
    config, selected, local, before = observation
    latest, latest_selected = configuration(binding(database, project, iteration)[0])
    require(policy(latest, latest_selected, database) == local and snapshot(database, project, iteration) == before,
            'Local inputs/ownership changed during capability observation.', 'readiness_inputs_changed')
    controls.resume_scope(database, project, iteration)
    from .restart import ownership
    item = assignments.current(database, project, iteration, config['operator_id'], require_active=False)
    ownership(database, item)


@contextmanager
def admission(database, project, iteration, *, resolution=None, selection=None):
    key = (database.execute('PRAGMA database_list').fetchone()[2], project, iteration)
    active = _active.get()
    if key in active:
        # Existing nested checkpoints may not perform remote I/O under the lane.
        if active[key] is not None:
            validate_selection(database, active[key][0], active[key][1], selection)
        yield
        return
    observation = observe(database, project, iteration, resolution=resolution, selection=selection)
    with controls.lane(database, project, iteration):
        compare(database, project, iteration, observation)
        token = _active.set({**active, key: observation})
        try:
            yield
        finally:
            _active.reset(token)


def check(database, project, iteration, *, resolution=None, selection=None):
    """Final pre-write check where the existing seam does not claim durable state."""
    with admission(database, project, iteration, resolution=resolution, selection=selection):
        controls.require_active(database, project, iteration, resolution=resolution)
