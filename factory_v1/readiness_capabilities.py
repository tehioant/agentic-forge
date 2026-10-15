"""Narrow host-only readers; no credential discovery, execution or grants."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import urllib.request

from . import assignments, diagnosis, planning_wakeup, requirement_execution, review, sandbox, service_dispatch
from .planning import require
from .repositories import GitHub, RepositoryError, unambiguous_fields
from .sandbox_source import physical

REMEDIATIONS = {
    'github_route_unsupported': 'Use the existing direct api.github.com classic OAuth capability; App, fine-grained and proxy permission introspection is unsupported.',
    'github_scopes_unavailable': 'Restore GitHub X-OAuth-Scopes metadata; accepted scopes or successful reads are not token permissions.',
    'github_scopes_insufficient': 'Select an already approved classic capability with repo and project scopes; inspection never grants scopes.',
    'github_permissions_insufficient': 'Restore effective push/read access and update permission on the exact private repository and ProjectV2.',
    'github_identity_changed': 'Reconcile the original endpoint, principal, repository and board; do not adopt replacements.',
    'native_planning_unavailable': 'Configure the existing private planning_config and original native planning binding.',
    'native_contract_unavailable': 'Install the reviewed native API source revision; do not repin automatically.',
    'execution_source_unavailable': 'Restore the installed built-in launcher, synthesis, provenance and independent review/diagnosis verifier sources.',
    'slot_capacity_unavailable': 'Select an unused approved slot for the requested stage; historical claims cannot be reused.',
    'slot_binding_changed': 'Reconcile the exact assignment/run/profile/home and scope before continuing.',
}

# Existing reviewed installed-file contracts (issue25 native API portability).
NATIVE_FILES = {
    'hermes_cli/plugin_host_child.py': 'd97b6355c7fd6c62a48e376f1c629125186f496a116447807bc202470b923b70',
    'hermes_cli/plugins_injection.py': 'ee162e06bef52f0cbacb5b234ee72498fb85399f34e0d050a8cda59ebdf5f0c6',
    'hermes_cli/plugins.py': 'a618a69581355e5fe56cb3d2250fc02647315359aae7fa0e6bd4dccc4ed3d3ad',
    'gateway/run_plugin_injection.py': 'fbac49a8dead1c3f1e665d89345ba2366ead90525ca16ae3d6f02ba441315bed',
    'gateway/run_inbound.py': '57f9bf1973231b1ba2d5b87afd7f6460be824f7d66bd5b299a783e8800ab3f0b',
    'gateway/run_turn.py': '1ba529d5dcd4279a40aa75bbccc6c225a8563b6a89667a69f48e651fb5e146fa',
    'agent/turn_context.py': 'ff9e1fc6de94b3c890b54a28b3133c473d5f9c905ecff04fcc5449877821095a',
}


def deny(code):
    raise RepositoryError(code, REMEDIATIONS[code])


class HostReaders:
    """Inspect the selected host routes, never invoke them. Not request-configurable."""
    def __init__(self, database, service, config, github, *, native_root=None):
        self.database, self.service, self.config, self.github = database, service, config, github
        self.native_root = native_root
        self.principal = None

    def _metadata(self, path, payload=None):
        # Use the same fixed credential/opener as the actual controller. Only the
        # OAuth scope header is retained, never authorization or arbitrary headers.
        github = self.github
        request = urllib.request.Request(github.base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + github.bearer,
                     'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
        with github.opener.open(request, timeout=github.timeout) as response:
            if response.geturl() != github.base + path or response.status != 200:
                deny('github_identity_changed')
            scopes = response.headers.get_all('X-OAuth-Scopes')
            if not scopes or len(scopes) != 1:
                deny('github_scopes_unavailable')
            scopes = frozenset(s.strip() for s in scopes[0].split(',') if s.strip())
            if not {'repo', 'project'} <= scopes:
                deny('github_scopes_insufficient')
            return json.load(response, object_pairs_hook=unambiguous_fields), scopes

    def github_authority(self, scope, operations):
        github = self.github
        if (type(github) is not GitHub or github.base != 'https://api.github.com' or
                not github.bearer.startswith(('gho_', 'ghp_'))):
            deny('github_route_unsupported')
        if scope['api_base'] != github.base or set(operations) != {'issues', 'projects', 'contents', 'pull_requests', 'merge'}:
            deny('github_identity_changed')
        # Retain the actual transport and credential only in host memory to catch
        # mid-observation replacement; neither enters the returned identity.
        route = (github.base, github.bearer, github.opener)
        user, scopes = self._metadata('/user')
        repo, repo_scopes = self._metadata('/repos/' + scope['repository'])
        owner, name = scope['repository'].split('/')
        query = '''query ReadinessAuthority($owner:String!,$name:String!,$board:ID!){
          viewer{id login} repository(owner:$owner,name:$name){databaseId nameWithOwner isPrivate viewerPermission}
          node(id:$board){... on ProjectV2{id closed viewerCanUpdate}}}'''
        result, graph_scopes = self._metadata('/graphql', {'query': query, 'variables': {
            'owner': owner, 'name': name, 'board': scope['board']['project_id']}})
        data = result.get('data', {})
        principal = (user.get('node_id'), user.get('login'))
        if (result.get('errors') or not all(isinstance(v, str) and v for v in principal) or
                data.get('viewer') != dict(zip(('id', 'login'), principal)) or
                (self.principal is not None and self.principal != principal) or
                route != (github.base, github.bearer, github.opener) or scopes != repo_scopes or scopes != graph_scopes):
            deny('github_identity_changed')
        expected = {'databaseId': scope['repository_id'], 'nameWithOwner': scope['repository'], 'isPrivate': True}
        if (any(data.get('repository', {}).get(k) != v for k, v in expected.items()) or
                repo.get('id') != scope['repository_id'] or repo.get('full_name') != scope['repository'] or
                repo.get('private') is not True or data.get('node', {}).get('id') != scope['board']['project_id']):
            deny('github_identity_changed')
        if (repo.get('archived') is not False or repo.get('disabled') is not False or
                repo.get('permissions', {}).get('pull') is not True or repo.get('permissions', {}).get('push') is not True or
                data['repository'].get('viewerPermission') not in {'WRITE', 'MAINTAIN', 'ADMIN'} or
                data['node'].get('closed') is not False or data['node'].get('viewerCanUpdate') is not True):
            deny('github_permissions_insufficient')
        self.principal = principal
        return assignments.digest({'scope': scope, 'principal': principal, 'oauth_scopes': sorted(scopes),
                                   'operations': sorted(operations), 'route': 'direct-classic-oauth'})

    def execution_support(self, scope, stages):
        if set(stages) != {'grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES}:
            deny('execution_source_unavailable')
        path = self.service.get('dispatch_config')
        if path is None:
            deny('native_planning_unavailable')
        from .readiness import load_config
        dispatch = load_config(path)
        if (any(dispatch.get(k) != scope[k] for k in ('project', 'iteration', 'operator_id')) or
                dispatch.get('synthesis_config') != self.config['synthesis_config'] or not dispatch.get('planning_config')):
            deny('native_planning_unavailable')
        row = self.database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                                    (scope['project'], scope['iteration'])).fetchone()
        item = json.loads(row[0])
        # A completed original producer needs no new wakeup allowance. Never
        # require a coordinator request or fabricate one just to inspect support.
        approval = planning_wakeup.configuration(dispatch['planning_config'],
            new_execution=item.get('planning', {}).get('status') != 'complete')
        identity = planning_wakeup.scope(item, approval)
        state = self.database.execute('PRAGMA database_list').fetchone()[2]
        if approval['binding'].get('state') != state or approval['binding'].get('operator_id') != scope['operator_id']:
            deny('native_planning_unavailable')
        root = self.native_root
        if root is None:
            spec = importlib.util.find_spec('hermes_cli')
            if spec is None or not spec.submodule_search_locations:
                deny('native_contract_unavailable')
            assert spec is not None and spec.submodule_search_locations
            root = Path(next(iter(spec.submodule_search_locations))).parent
        sources = {}
        for relative, expected in NATIVE_FILES.items():
            try:
                source = physical(Path(root) / relative)
                info = source.stat()
                actual = hashlib.sha256(source.read_bytes()).hexdigest()
                if info.st_uid != os.getuid() or info.st_mode & 0o022 or actual != expected:
                    deny('native_contract_unavailable')
                sources[relative] = actual
            except (OSError, RepositoryError):
                deny('native_contract_unavailable')
        # These are the installed built-in verifiers/launchers, not arbitrary
        # callback names or future candidate authorizations. Pin actual files.
        for function in (service_dispatch._launch_child, requirement_execution.invoke, sandbox.launch,
                         sandbox.check_worker_evidence, review.execution_binding, review.authorization,
                         diagnosis.execution_binding, diagnosis.authorization):
            source = physical(function.__code__.co_filename)
            info = source.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                deny('execution_source_unavailable')
            sources[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
        synthesis = requirement_execution.configuration(self.database, self.config['synthesis_config'], scope['operator_id'])
        return assignments.digest({'native': identity, 'sources': sources, 'synthesis': synthesis})
