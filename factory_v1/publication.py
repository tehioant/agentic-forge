"""Read back controller-published documentation; never mutate GitHub or gates."""
import base64
import hashlib
import json
import re

from .repositories import RepositoryError, reconcile, save_iteration
from .specifications import export_spec


def verify_spec(database, item, revision, commit, github):
    database.execute('BEGIN IMMEDIATE')
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (item['project_id'], item['iteration_id'])).fetchone()
    item = json.loads(row[0])
    package = export_spec(item, revision)
    if not isinstance(commit, str) or re.fullmatch(r'[0-9a-f]{40}', commit) is None:
        raise RepositoryError('publication_mismatch', 'Use an exact immutable full Git commit SHA, never a branch or repaired token.')
    if 'skills' not in item['interview']['record']:
        raise RepositoryError('skill_blocked', 'Configure and pin all fresh required stage skills and transitive dependencies before handoff.')
    from .skills import resolve
    resolution = resolve(item['interview']['record']['skills'])
    spec = item['specification']
    reconcile(database, item, github)
    observation = github.request('/repos/' + item['repository'] + '/git/commits/' + commit)
    if not isinstance(observation, dict) or observation.get('sha') != commit:
        raise RepositoryError('publication_mismatch', 'Exact publication commit could not be read back.')
    blobs = {}
    for path, content in package['files'].items():
        metadata = github.request('/repos/' + item['repository'] + '/contents/' + path + '?ref=' + commit)
        if (not isinstance(metadata, dict) or metadata.get('type') != 'file' or metadata.get('path') != path or
                metadata.get('encoding') != 'base64' or not isinstance(metadata.get('content'), str)):
            raise RepositoryError('publication_mismatch', 'Read back the exact documentation files at the pinned commit, not symlinks or download URLs.')
        try:
            raw = base64.b64decode(metadata['content'].replace('\n', ''), validate=True)
        except ValueError as error:
            raise RepositoryError('publication_mismatch', 'Published documentation encoding is invalid.') from error
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if raw != content.encode() or metadata.get('sha') != blob:
            raise RepositoryError('publication_mismatch', 'Published documentation differs from the exact interview-derived specification.')
        blobs[path] = blob
    spec['status'] = 'verified'
    spec['publication'] = {'repository': item['repository'], 'commit': commit,
                           'api_base': github.base, 'blobs': blobs}
    from .settlement import active_decisions
    item['handoff'] = {'stage': 'ticket_synthesis', 'batch_approval_required': False,
                       'spec_reference': {'repository': item['repository'], 'commit': commit,
                                          'path': spec['docs_path'] + '/milestone.json', 'revision': revision},
                       'decision_ids': [d['id'] for d in active_decisions(item['interview']['record']['decisions']) if d['scope'] == 'current'],
                       'skills': resolution}
    item['stage'] = 'ticket_synthesis_ready'
    item['correlation']['stage'] = item['stage']
    save_iteration(database, item)
    return item
