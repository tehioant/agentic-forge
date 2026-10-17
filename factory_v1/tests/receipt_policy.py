"""Small issuer-policy fixture; no readiness/worker/service execution journey."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory_v1 import controls, readiness_gate, service
from factory_v1 import continuation_dispatch
from factory_v1.repositories import RepositoryError


@contextmanager
def issued_policy(case, database, path, kind, *, future=()):
    """Seal absent slots, issue supplied explicit bytes, mock only issuer I/O.

    Pause is an independent prerequisite observation; actual registration, sealing,
    policy hashing, receipt identity storage and consumer validation remain real.
    """
    path = Path(path)
    raw = path.read_bytes()
    path.unlink()
    state = Path(database.execute('PRAGMA database_list').fetchone()[2])
    state.chmod(0o600)
    root = path.parent / ('issuer-' + kind)
    root.mkdir(mode=0o700)
    verifier = root / 'verifier'
    verifier.write_bytes(b'LABELED reviewed external verifier fixture; never executed')
    verifier.chmod(0o700)
    item = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
    project, iteration = item['project_id'], item['iteration_id']
    config = dict.fromkeys(service.FIELDS)
    config.update(state=str(state), project=project, iteration=iteration,
                  operator_id=item['approval']['operator_id'], readiness_config=str(root / 'readiness.json'),
                  dispatch_config=str(root / 'dispatch.json'))
    paths = [str(path), *map(str, future)]
    selected = {'runtime_receipts': {'scope': readiness_gate.scope_identity(item),
        'verifier': {'path': str(verifier), 'sha256': hashlib.sha256(verifier.read_bytes()).hexdigest()},
        'receipts': {kind: {p: {'fixture': 'exact scoped external issuance'} for p in paths}}}}
    def write(destination, value):
        destination.write_text(json.dumps(value))
        destination.chmod(0o600)
    selected_path = root / 'readiness.json'
    write(selected_path, selected)
    write(root / 'dispatch.json', {'continuation': {'authorities': {kind: paths}}})
    service_path = root / 'service.json'
    write(service_path, config)
    database.execute('CREATE TABLE IF NOT EXISTS model_grants '
                     '(project TEXT, iteration TEXT, grant_id TEXT, remaining INTEGER)')
    database.commit()
    readiness_gate.register(database, str(service_path), config)
    with patch.object(controls, 'blocked', return_value=True):
        readiness_gate.seal(database, str(service_path))
    sealed = readiness_gate.binding(database, project, iteration)[1]
    path.write_bytes(raw)
    path.chmod(0o600)
    calls = []
    def verify(argv, **kwargs):
        case.assertEqual(argv, [str(verifier)])
        request = json.loads(kwargs['input'])
        case.assertEqual(request['kind'], kind)
        case.assertEqual(request['scope'], selected['runtime_receipts']['scope'])
        case.assertIn(request['path'], paths)
        case.assertEqual(request['sha256'], hashlib.sha256(Path(request['path']).read_bytes()).hexdigest())
        # A subprocess verifier must never run while the interruption lane is held.
        lane_root, _ = controls.paths(database, project, iteration)
        with (lane_root / 'lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        calls.append(request)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'verified': request}).encode())
    fixture = SimpleNamespace(config=config, selected=selected, selected_path=selected_path,
                              sealed=sealed, calls=calls, project=project, iteration=iteration)
    case.assertEqual(readiness_gate.policy(config, selected, database), sealed)
    with patch('factory_v1.readiness_receipts.subprocess.run', side_effect=verify):
        yield fixture


def consumer_matrix(case, database, path, kind, consume):
    """Exercise real finite selection then the actual consuming public boundary."""
    path = Path(path)
    future = path.parent / ('future-' + kind + '.json')
    with issued_policy(case, database, path, kind, future=[future]) as issuer:
        conf = {'project': issuer.project, 'iteration': issuer.iteration,
                'operator_id': issuer.config['operator_id'], 'continuation': {
                    'authorities': {k: [str(future), str(path)] for k in continuation_dispatch.KINDS}}}
        approved = json.loads(path.read_bytes())
        def select(selected_kind=kind):
            return continuation_dispatch.authority(database, conf, selected_kind, lambda value: value == approved)
        case.assertEqual(select(), str(path))  # Approved absence is skipped, not consumed.
        result = consume()
        case.assertGreaterEqual(len(issuer.calls), 2)  # Both real boundaries freshly verify.
        case.assertEqual(readiness_gate.policy(issuer.config, issuer.selected, database), issuer.sealed)
        before = list(database.iterdump())
        with case.assertRaises(RepositoryError):
            select(next(k for k in continuation_dispatch.KINDS if k != kind))
        conf['continuation']['authorities'][kind].insert(0, str(path.parent / 'unapproved-absent.json'))
        with case.assertRaises((RepositoryError, OSError)):
            select()
        conf['continuation']['authorities'][kind].pop(0)
        raw = path.read_bytes()
        path.write_text(json.dumps({**approved, 'execution_reference': 'replaced accepted identity'}))
        with case.assertRaises(RepositoryError):
            consume()  # A caller cannot bypass selection's immutable identity check.
        path.write_bytes(raw)
        selected_raw = issuer.selected_path.read_bytes()
        drift = json.loads(selected_raw)
        drift['runtime_receipts']['scope'] = '0' * 64
        issuer.selected_path.write_text(json.dumps(drift))
        with case.assertRaises(RepositoryError):
            consume()
        issuer.selected_path.write_bytes(selected_raw)
        case.assertEqual(list(database.iterdump()), before)
        return result
