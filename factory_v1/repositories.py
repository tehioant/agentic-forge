"""Metadata-only GitHub onboarding through a configured controller capability."""
import fcntl
from contextlib import contextmanager
import http.client
import json
import math
import re
import urllib.parse
import urllib.error
import urllib.request


def valid_repository(value):
    """Match the exact intake owner/repository grammar without normalization."""
    return (isinstance(value, str)
            and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/[A-Za-z0-9_.-]{1,100}', value) is not None
            and value.split('/')[-1] not in {'.', '..'})


class RepositoryError(Exception):
    def __init__(self, code, message, http_status=None):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unambiguous_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Ambiguous GitHub metadata')
        result[key] = value
    return result


class GitHub:
    def __init__(self, base, bearer, timeout):
        try:
            url = urllib.parse.urlsplit(base)
            valid = (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in {'127.0.0.1', '::1', 'localhost'}))
            valid = valid and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment
            valid = valid and (url.port is None or url.port > 0) and math.isfinite(timeout) and 0 < timeout <= 300
            valid = valid and not any(char.isspace() for char in base) and '\r' not in bearer and '\n' not in bearer
        except ValueError:
            valid = False
        if not valid:
            raise RepositoryError('invalid_capability', 'Configure an explicit HTTPS GitHub capability or loopback HTTP broker, no URL credentials/query/fragment, and a finite timeout in (0, 300].')
        self.base = base.rstrip('/')
        self.bearer = bearer
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, body=None):
        request = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body).encode(), headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + self.bearer, 'Accept': 'application/vnd.github+json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                metadata = json.load(response, object_pairs_hook=unambiguous_fields)
                # None is reserved for explicit HTTP 404, never JSON null.
                if metadata is None:
                    raise ValueError('Null successful GitHub metadata')
                return metadata
        except urllib.error.HTTPError as error:
            if error.code == 404 and body is None:
                return None
            raise RepositoryError('github_blocked',
                                  f'GitHub HTTP {error.code}; request scoped repository metadata/creation permission or resolution of a name/plan/protection conflict; do not purchase a plan or bypass protection.', error.code) from error
        except (OSError, ValueError, RecursionError, http.client.HTTPException) as error:
            raise RepositoryError('github_unavailable', 'GitHub metadata unavailable; restore the configured capability and retry.') from error


@contextmanager
def state_lock(database):
    """Share onboarding's lock; acquire before SQLite reads/transactions."""
    state_path = database.execute('PRAGMA database_list').fetchone()[2]
    with open(state_path + '.onboarding.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def onboard(database, item, github, receipt=None):
    # Independent of SQLite transactions: the pending intent must commit while
    # concurrent controllers remain excluded. Process death releases this lock.
    # Commit the final payload before releasing it as well.
    with state_lock(database), database:
        row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                               (item['project_id'], item['iteration_id'])).fetchone()
        return reconcile(database, json.loads(row[0]), github, receipt)


def save_iteration(database, item):
    """Store the payload without changing the caller's transaction boundary."""
    database.execute('UPDATE iterations SET payload=? WHERE project_id=? AND iteration_id=?',
                     (json.dumps(item), item['project_id'], item['iteration_id']))


def block_creation(database, item, error):
    """Retain definitive creation blockers across transaction rollback/restart."""
    item['repository_onboarding']['status'] = 'blocked'
    item['repository_onboarding']['blocker'] = {'code': error.code, 'message': str(error)}
    save_iteration(database, item)
    database.commit()


def reconcile(database, item, github, receipt=None):
    previous = item.get('repository_onboarding')
    if receipt is not None:
        intent = item.get('repository_intent')
        valid = (isinstance(receipt, dict) and
                 set(receipt) == {'repository', 'repository_id', 'marker', 'api_base', 'reference'} and
                 intent is not None and receipt.get('repository') == item['repository'] and
                 receipt.get('marker') == intent['marker'] and receipt.get('api_base') == github.base and
                 type(receipt.get('repository_id')) is int and receipt['repository_id'] > 0 and
                 isinstance(receipt.get('reference'), str) and bool(receipt['reference'].strip()))
        if not valid:
            raise RepositoryError('invalid_receipt', 'Recovery requires a trusted creation receipt binding exact repository, positive repository ID, approved marker, capability endpoint and evidence reference.')
    if previous and previous.get('api_base') != github.base:
        raise RepositoryError('capability_conflict', 'Use the exact recorded GitHub capability endpoint for reconciliation; migration requires controller review.')
    if previous and previous.get('status') == 'blocked':
        raise RepositoryError(previous['blocker']['code'], previous['blocker']['message'])
    metadata = github.request('/repos/' + item['repository'])
    owner, name = item['repository'].split('/')
    intent = item.get('repository_intent')
    if intent:
        identity = github.request('/user')
        if not isinstance(identity, dict) or identity.get('login') != owner:
            raise RepositoryError('account_capability_required', 'Selected account differs from configured GitHub identity; supply a reviewed account-scoped creation capability.')
        previous = item.get('repository_onboarding')
        if metadata is not None and previous is None and receipt is None:
            raise RepositoryError('repository_collision', 'The new-product target already exists; do not adopt it or create a replacement.')
        if metadata is None and (previous is not None or receipt is not None):
            raise RepositoryError('creation_uncertain', 'Previously attempted target is absent; request controller investigation of the exact creation outcome before authorizing any retry.')
        if metadata is None:
            item['repository_onboarding'] = {'status': 'pending', 'mode': 'new',
                                            'marker': intent['marker'], 'api_base': github.base}
            save_iteration(database, item)
            database.commit()  # Durable before the first byte of the external mutation.
            try:
                created = github.request('/user/repos', {'name': name, 'private': True,
                                                       'description': intent['marker'], 'auto_init': False})
            except RepositoryError as error:
                if error.http_status in {401, 403, 404, 409, 422}:
                    block_creation(database, item, error)
                    raise
                raise RepositoryError('creation_uncertain', 'Creation outcome requires exact target reconciliation before retry. ' + str(error)) from error
            if not isinstance(created, dict) or type(created.get('id')) is not int or created['id'] <= 0:
                error = RepositoryError('repository_mismatch', 'Successful creation returned no valid immutable repository ID; request controller investigation before adopting any target.')
                block_creation(database, item, error)
                raise error
            item['repository_onboarding']['created_repository_id'] = created['id']
            save_iteration(database, item)
            database.commit()  # Pin the returned identity before read-back or restart.
            try:
                metadata = github.request('/repos/' + item['repository'])
            except RepositoryError as error:
                raise RepositoryError('creation_uncertain', 'Creation returned successfully but read-back is unavailable; restore metadata access and reconcile the exact target. ' + str(error)) from error
    if (not isinstance(metadata, dict) or
            metadata.get('full_name') != item['repository'] or
            metadata.get('name') != name or
            not isinstance(metadata.get('owner'), dict) or
            metadata['owner'].get('login') != owner or
            type(metadata.get('private')) is not bool or
            type(metadata.get('id')) is not int or metadata['id'] <= 0):
        raise RepositoryError('repository_mismatch', 'Exact selected repository identity/privacy could not be verified; no replacement is permitted.')
    previous = item.get('repository_onboarding')
    if previous and 'created_repository_id' in previous and metadata['id'] != previous['created_repository_id']:
        raise RepositoryError('repository_mismatch', 'Repository ID differs from the successful creation response; do not adopt the replacement.')
    if previous and previous.get('status') == 'verified' and metadata['id'] != previous['metadata']['id']:
        raise RepositoryError('repository_mismatch', 'Repository identity changed since verification; do not adopt the replacement.')
    if intent and (metadata['private'] is not True or metadata.get('description') != intent['marker']):
        raise RepositoryError('repository_mismatch', 'Created repository must be private and bear the exact approved ownership marker.')
    if receipt is not None and metadata['id'] != receipt['repository_id']:
        raise RepositoryError('repository_mismatch', 'Repository ID differs from the trusted creation receipt; do not adopt the target.')
    metadata = {key: metadata.get(key) for key in ['id', 'full_name', 'name', 'private', 'description']}
    metadata['owner'] = {'login': owner}
    item['repository_onboarding'] = {'status': 'verified', 'mode': 'new' if intent else 'existing', 'api_base': github.base, 'metadata': metadata}
    if receipt is not None:
        item['repository_onboarding']['creation_receipt'] = receipt
    elif previous and 'creation_receipt' in previous:
        item['repository_onboarding']['creation_receipt'] = previous['creation_receipt']
    save_iteration(database, item)
    return item
