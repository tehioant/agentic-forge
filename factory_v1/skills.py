"""Explicit fresh skill closure and factory stage policy; no ambient discovery."""
import hashlib
import re
from pathlib import Path

from .repositories import RepositoryError

STAGES = ('grilling', 'specification', 'tickets', 'implementation', 'simplification', 'review')
GATES = ('operator_answers_and_agent_judgment', 'immutable_document_readback', 'current_milestone_only',
         'requirements_and_tests', 'preserve_behavior', 'independent_requirement_review')
ADAPTABLE = {'routine_spec_approval', 'routine_ticket_batch_approval', 'nested_duplicate_review'}


def check(condition, message):
    if not condition:
        raise RepositoryError('skill_blocked', message)


def name_valid(name):
    return isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name) is not None


def resolve(configuration):
    check(isinstance(configuration, dict) and set(configuration) == {'catalog', 'stages'}, 'Supply an explicit fresh skill catalog and stage roots.')
    check(isinstance(configuration['catalog'], list) and isinstance(configuration['stages'], dict) and
          set(configuration['stages']) == set(STAGES), 'Resolve every required factory stage at startup.')
    catalog = {}
    for skill in configuration['catalog']:
        check(isinstance(skill, dict) and set(skill) == {'name', 'path', 'sha256', 'dependencies', 'implicit_loops'}, 'Skill descriptors must be unambiguous and pinned.')
        check(name_valid(skill['name']) and skill['name'] not in catalog, 'Skill names must resolve uniquely, including transitive dependencies.')
        check(isinstance(skill['path'], str) and Path(skill['path']).is_absolute() and
              isinstance(skill['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', skill['sha256']) is not None, 'Pin each explicit skill file with a SHA-256 digest.')
        check(isinstance(skill['dependencies'], list) and all(name_valid(n) for n in skill['dependencies']) and
              len(set(skill['dependencies'])) == len(skill['dependencies']), 'Use exact unique dependency names.')
        check(isinstance(skill['implicit_loops'], list) and all(isinstance(n, str) and n in ADAPTABLE for n in skill['implicit_loops']),
              'Only routine batch approvals and duplicate nested review loops may be adapted; required gates remain authoritative.')
        catalog[skill['name']] = skill
    resolved = {}
    visiting = set()

    def visit(name):
        check(name_valid(name) and name in catalog, 'Required skill/dependency is absent; configure its exact fresh descriptor.')
        check(name not in visiting, 'Cyclic skill dependency prevents a bounded stage contract.')
        if name in resolved:
            return
        visiting.add(name)
        skill = catalog[name]
        try:
            path = Path(skill['path'])
            check(path.is_file() and not path.is_symlink(), 'Skill must be an explicit regular fresh file, not a symlink.')
            content = path.read_bytes()
        except OSError as error:
            raise RepositoryError('skill_blocked', 'Configured fresh skill file is unavailable.') from error
        check(hashlib.sha256(content).hexdigest() == skill['sha256'], 'Configured skill content differs from its pinned digest.')
        for dependency in skill['dependencies']:
            visit(dependency)
        visiting.remove(name)
        resolved[name] = skill

    try:
        for name in configuration['stages'].values():
            visit(name)
    except RecursionError as error:
        raise RepositoryError('skill_blocked', 'Skill dependency graph exceeds supported traversal limits.') from error
    contracts = {}
    for stage, gate in zip(STAGES, GATES):
        name = configuration['stages'][stage]
        closure = set()

        def include(skill_name):
            if skill_name not in closure:
                closure.add(skill_name)
                for dependency in catalog[skill_name]['dependencies']:
                    include(dependency)

        include(name)
        loops = sorted({loop for n in closure for loop in catalog[n]['implicit_loops']})
        # Preserve the descriptor's order for the root's documented adaptations.
        root_loops = catalog[name]['implicit_loops']
        loops = list(dict.fromkeys(root_loops + loops))
        contracts[stage] = {'skill': name, 'gate': gate, 'routine_approval': False, 'adapted_loops': loops,
                            'dependencies': sorted(closure - {name})}
    return {'resolved': resolved, 'contracts': contracts}
