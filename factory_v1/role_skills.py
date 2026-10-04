"""Reviewed installed-source identities and minimal stage closures, never ambient discovery."""
import hashlib
from pathlib import Path

from .planning import require
from .repositories import RepositoryError

SELECTED = {
    'implement': ('/home/ops/.hermes/skills/implement/SKILL.md', '6b13bcb6119df090c97be3c8a90560071a26f21f4fc6ad0facde97b25ecb1da0'),
    'tdd': ('/home/ops/.hermes/skills/tdd/SKILL.md', '93ea419b76e9caaf26153b828e984f7c3fb136f4caa67b14af95f32ea965a1cc'),
    'codebase-design': ('/home/ops/.hermes/skills/codebase-design/SKILL.md', '2c20617f87ec8af6a434859f381b2f061a69b530444e74eb39e78bb016a6d1e2'),
    'code-review': ('/home/ops/.hermes/skills/code-review/SKILL.md', '47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50'),
    'simplify-code': ('/home/ops/.hermes/skills/software-development/simplify-code/SKILL.md', '9ee977c3362a1c07f2e327198e066ddb3c0ed67709706fc410165ee820b2ca01'),
    'diagnosing-bugs': ('/home/ops/.hermes/skills/diagnosing-bugs/SKILL.md', '9168404abda0967a5d32977e3498cd95fda6807018852f3de736a78357c82b40'),
    'tdd/tests.md': ('/home/ops/.hermes/skills/tdd/tests.md', '859f9e592c188fda4fc7277dd180e4ce9c7a2e13f6efe1f6f29eccc9d28c106a'),
    'tdd/mocking.md': ('/home/ops/.hermes/skills/tdd/mocking.md', '3ceb807fdf4a47d6a93d4d9a891e5ba6d362a6247bd08adc451feebfc17361ef'),
}
DEPENDENCIES = {
    'tdd': ['codebase-design', 'tdd/tests.md', 'tdd/mocking.md'],
}
STAGES = {
    'implementation': ('implement',),
    'corrections': ('implement',),
    'simplify': ('simplify-code',),
    'review-standards': ('code-review',),
    'review-spec': ('code-review',),
    'diagnosis': ('diagnosing-bugs',),
    'repair': ('diagnosing-bugs', 'implement'),
}
ADAPTATIONS = {
    'implementation': 'factory-implement-v1:meaningful-tests;optional-test-first;later-two-axis-review;controller-commits',
    'corrections': 'factory-corrections-v1:implement;exact-findings;meaningful-tests;later-two-axis-review;controller-commits',
    'simplify': 'factory-simplify-v1:inline-reuse-quality-efficiency-altitude;preserve-behavior;evidence-backed-no-op',
    'review-standards': 'factory-review-standards-v1:fresh-read-only;exact-baseline-candidate;repo-standards-and-smells;separate-verdict',
    'review-spec': 'factory-review-spec-v1:fresh-read-only;exact-baseline-candidate;original-spec;separate-verdict',
    'diagnosis': 'factory-diagnosis-v1:read-only;feedback-loop-hypotheses-commands-conclusions;stop-before-modifying',
    'repair': 'factory-repair-v1:diagnosing-bugs-then-implement;regression-cleanup;same-simplify-review-gates;controller-commits',
}


SUPPORT_POLICY = {
    'required': 'Only the reviewed transitive closure; no ambient bare-name discovery.',
    'implement-review': 'The updated implement /code-simplifier instruction maps explicitly to the existing later simplify-code stage, followed by separate Standards and Spec reviews. No nested simplification/review; controller owns commits, push and shipping.',
    'codebase-design': 'Vocabulary reference only. DEEPENING.md and DESIGN-IT-TWICE.md are optional unselected workflows; request a new reviewed closure before using them.',
    'simplify-code': 'Related-skill metadata and optional delegation are not dependencies; substantive four-angle cleanup runs inline. Acceptance review is separate.',
    'diagnosing-bugs': 'No HITL template was supplied. That optional branch must report unavailable capability, not invent or execute a replacement.',
    'repository-inputs': 'Tracker, standards, specs and preceding artifacts are explicit pinned handoff inputs, not ambient dependency discovery.',
}
STAGE_RULES = {
    'implementation': ['Run implement for this ticket, including meaningful tests at the agreed public seams; test-first ordering is optional.',
                       'Nested review belongs to the dedicated later two-axis review; controller owns commits and publication.'],
    'corrections': ['Run implement for only the pinned candidate and preceding findings, retaining corrections and regression evidence.',
                    'Meaningful tests are mandatory; no hidden nested review or worker commits.'],
    'simplify': ['Run simplify-code in one fresh context covering reuse, quality, efficiency and altitude inline, with no fan-out.',
                 'Preserve behavior and scope; retain findings, verified cleanup or evidence-backed no-op.'],
    'review-standards': ['Load code-review and inspect actual exact baseline/candidate independently in a fresh read-only context.',
                         'Execute only Standards, including documented repo rules and the skill smell baseline; retain separate findings and verdict.'],
    'review-spec': ['Load code-review and inspect actual exact baseline/candidate independently in a fresh read-only context.',
                   'Execute only Spec against original pinned requirements; retain separate findings and verdict, never skip for lack of spec.'],
    'diagnosis': ['Run diagnosing-bugs with actual reproduction/feedback loop, ranked hypotheses, diagnostic commands and conclusions.',
                  'Read-only diagnosis stops before modifying phases; honestly report missing capabilities and request attention through the controller.'],
    'repair': ['Run diagnosing-bugs then implement for only the scoped incident; retain actual feedback loop, regression tests and cleanup evidence.',
               'Controller owns commits; repair must later pass simplify-code, separate Standards/Spec review and unchanged delivery gates.'],
}


def closure(stage):
    require(isinstance(stage, str) and stage in STAGES, 'Unknown bounded worker stage.', 'invalid_assignment')
    needed = set()
    def visit(name):
        if name not in needed:
            needed.add(name)
            for dependency in DEPENDENCIES.get(name, []):
                visit(dependency)
    for entry in STAGES[stage]:
        visit(entry)
    return sorted(needed)


def snapshots(stage, configuration):
    needed = closure(stage)
    require(isinstance(configuration, list) and len(configuration) == len(needed),
            'Configure only the exact needed installed-source closure.', 'skill_blocked')
    resolved = {}
    for descriptor in configuration:
        require(isinstance(descriptor, dict) and set(descriptor) == {'name', 'source', 'path', 'sha256', 'dependencies'},
                'Each selected source needs explicit identity, bytes pin and dependencies.', 'skill_blocked')
        name = descriptor['name']
        require(isinstance(name, str) and name in needed and name not in resolved,
                'Unknown, duplicate or unneeded skill selection.', 'skill_blocked')
        source, pin = SELECTED[name]
        require(descriptor['source'] == source and descriptor['sha256'] == pin and
                descriptor['dependencies'] == DEPENDENCIES.get(name, []) and
                isinstance(descriptor['path'], str) and Path(descriptor['path']).is_absolute(),
                'Selected identity, reviewed pin or dependency graph differs.', 'skill_blocked')
        path = Path(descriptor['path'])
        try:
            require(path.is_file() and not any(p.is_symlink() for p in [path, *path.parents]),
                    'Selected source must be a regular non-symlink file.', 'skill_blocked')
            raw = path.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == pin, 'Selected skill input bytes changed.', 'skill_blocked')
            resolved[name] = {**descriptor, 'instructions': raw.decode('utf-8')}
        except (OSError, UnicodeError) as error:
            raise RepositoryError('skill_blocked', 'Selected installed skill input unavailable.') from error
    require(set(resolved) == set(needed), 'Missing transitive skill input.', 'skill_blocked')
    return [resolved[name] for name in needed]
