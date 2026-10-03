"""Fresh originating-conversation interview and specification lifecycle."""
import hashlib
import json
import re

from .repositories import RepositoryError, save_iteration


class SpecificationError(RepositoryError):
    pass


def text(value):
    return isinstance(value, str) and bool(value.strip())


def fields(value, names):
    return isinstance(value, dict) and set(value) == set(names.split())


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value) is not None


def require(condition, message):
    if not condition:
        raise SpecificationError('invalid_interview', message)


CATEGORIES = {'vision', 'milestone', 'programming_language', 'user_facing_language', 'stack',
              'requirements', 'constraints', 'non_goals', 'acceptance', 'testing'}


def validate(record, item, operator_id):
    require(isinstance(record, dict) and fields({key: value for key, value in record.items() if key not in {'skills', 'synthesis'}},
                                              'origin expected_revision questions decisions judgment'), 'Provide only fresh interview fields.')
    require(record['origin'] == item['origin'], 'Interview must belong to the exact originating conversation.')
    require(type(record['expected_revision']) is int and record['expected_revision'] >= 0, 'Use a nonnegative integer revision.')
    judgment = record['judgment']
    require(fields(judgment, 'sufficient rationale reference') and type(judgment['sufficient']) is bool and
            text(judgment['rationale']) and text(judgment['reference']), 'Record agent sufficiency judgment with provenance.')
    require(isinstance(record['questions'], list) and bool(record['questions']), 'Retain interview questions.')
    seen = set()
    for question in record['questions']:
        require(fields(question, 'id question challenge recommendation answer'), 'Each question requires a challenge and recommendation.')
        require(identifier(question['id']) and question['id'] not in seen, 'Question IDs must be unique.')
        seen.add(question['id'])
        require(all(text(question[key]) for key in ['question', 'challenge', 'recommendation']), 'Retain substantive interview text.')
        answer = question['answer']
        require(answer is None or (fields(answer, 'operator_id reference text') and answer['operator_id'] == operator_id and
                                  text(answer['reference']) and text(answer['text'])), 'Answers require actual operator provenance; silence is not a choice.')
    require(isinstance(record['decisions'], list), 'Retain decisions as a list.')
    questions = {q['id']: q for q in record['questions']}
    seen = set()
    for decision in record['decisions']:
        require(isinstance(decision, dict) and fields({k: v for k, v in decision.items() if k != 'settled'},
                                                    'id category value question_id scope'), 'Decisions require scope and question provenance.')
        require(identifier(decision['id']) and decision['id'] not in seen, 'Decision IDs must be unique.')
        seen.add(decision['id'])
        require(isinstance(decision['category'], str) and decision['category'] in CATEGORIES and
                isinstance(decision['scope'], str) and decision['scope'] in {'current', 'future'}, 'Use explicit decision categories and current/future scopes.')
        require(isinstance(decision['question_id'], str) and decision['question_id'] in questions, 'Decision must cite a recorded question.')
        answer = questions[decision['question_id']]['answer']
        require(answer is not None and decision['value'] == answer['text'], 'A decision retains the actual operator answer, not an invented recommendation.')
        from .settlement import validate_settled
        validate_settled(decision, questions, require, fields, text)
    from .settlement import active_decisions, validate_reconciliation
    validate_reconciliation(record['decisions'], require)
    active = active_decisions(record['decisions'])
    resolved = {d['category'] for d in active
                if (d['scope'] == 'current' or d['category'] == 'vision')
                and d.get('settled', {}).get('status') in {'resolved', 'inapplicable'}}
    pending = {d['category'] for d in active if d['scope'] == 'current'
               and d.get('settled', {}).get('status') not in {'resolved', 'inapplicable'}}
    # Distinct current language meanings require explicit reconciliation.
    conflicts = {category for category in {'programming_language', 'user_facing_language'}
                 if len({d.get('settled', {}).get('meaning', d['value']) for d in active
                         if d['category'] == category and d['scope'] == 'current'}) > 1}
    from .settlement import validate_synthesis
    unresolved = sorted((CATEGORIES - resolved) | conflicts | pending)
    unresolved += [q['id'] for q in record['questions'] if q['answer'] is None]
    return list(dict.fromkeys(unresolved + validate_synthesis(record, questions, require, fields, text)))


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def export_spec(item, revision):
    spec = item.get('specification')
    if spec is None or spec['revision'] != revision:
        raise SpecificationError('specification_conflict', 'Select the exact current sufficient specification revision.')
    return {'repository': item['repository'], 'revision': revision,
            'files': {spec['docs_path'] + '/' + name: content for name, content in spec['documents'].items()},
            'capability_request': spec['blocker']}


def interview(database, item, record, operator_id):
    database.execute('BEGIN IMMEDIATE')
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (item['project_id'], item['iteration_id'])).fetchone()
    item = json.loads(row[0])
    if item.get('repository_onboarding', {}).get('status') != 'verified':
        raise SpecificationError('onboarding_required', 'Read back the approved repository through onboarding before beginning its interview.')
    unresolved = validate(record, item, operator_id)
    previous = item.get('interview')
    if previous and previous['record'] == record:
        return item
    revision = previous['revision'] if previous else 0
    if record['expected_revision'] != revision:
        raise SpecificationError('interview_conflict', 'Read the current revision before submitting changed interview input.')
    if 'specification' in item:
        item.setdefault('specification_history', []).append(item.pop('specification'))
    item.pop('handoff', None)
    item['execution_allowed'] = False
    history = previous['history'] + [{key: previous[key] for key in ['revision', 'record', 'status']}] if previous else []
    item['interview'] = {'status': 'pending', 'revision': revision + 1, 'record': record, 'history': history, 'unresolved': unresolved}
    if 'skills' in record:
        from .skills import resolve
        try:
            item['interview']['skills'] = resolve(record['skills'])
        except RepositoryError as error:
            raise SpecificationError(error.code, str(error)) from error
    item['stage'] = 'interview_pending'
    if record['judgment']['sufficient'] and not unresolved:
        item['interview']['status'] = 'sufficient'
        identity = {key: item[key] for key in ['project_id', 'iteration_id', 'repository', 'origin']}
        from .settlement import meanings
        documents = {
            'vision.json': canonical({**identity, 'decisions': record['decisions'],
                                      'settled_decisions': meanings(record['decisions']),
                                      'content': record.get('synthesis', {}).get('vision')}),
            'milestone.json': canonical({**identity, 'decisions': [d for d in record['decisions'] if d['scope'] == 'current'],
                                         'settled_decisions': meanings([d for d in record['decisions'] if d['scope'] == 'current']),
                                         'content': record.get('synthesis', {}).get('milestone')}),
            'decisions.json': canonical(record),
        }
        digest = hashlib.sha256(canonical(documents).encode()).hexdigest()
        item['specification'] = {'status': 'publication_blocked', 'revision': digest,
                                 'interview_revision': revision + 1, 'documents': documents,
                                 'docs_path': f"docs/factory/{item['iteration_id']}/{digest}",
                                 'blocker': 'Install a reviewed controller capability to publish these exact documentation files in the selected repository and read them back at an immutable commit; no mutation is authorized by this CLI.'}
        item['stage'] = 'specification_publication_blocked'
    item['correlation']['stage'] = item['stage']
    save_iteration(database, item)
    return item
