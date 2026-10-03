"""Explicit agent interpretation, separate from immutable operator provenance.

This trusted handoff validates attribution, not natural-language truth. It never
tries to detect acceptance, deferment or a language choice from transcript words.
"""


def validate_settled(decision, questions, require, fields, text):
    settled = decision.get('settled')
    if settled is None:
        return
    require(fields(settled, 'status meaning basis question_ids rationale supersedes'),
            'Settled decisions require explicit meaning, resolution and supporting questions.')
    require(settled['status'] in {'resolved', 'unresolved', 'deferred', 'inapplicable'},
            'Use an explicit resolution/applicability status.')
    require(settled['basis'] in {'operator_answer', 'accepted_recommendation', 'clarification'},
            'Record the basis of the agent interpretation.')
    require(text(settled['meaning']) and text(settled['rationale']),
            'Record settled meaning and the reason for resolution/applicability.')
    ids = settled['question_ids']
    require(isinstance(ids, list) and bool(ids) and all(isinstance(q, str) and q in questions for q in ids)
            and len(ids) == len(set(ids)) and decision['question_id'] in ids,
            'Settled meaning must cite its original question and supporting questions.')
    require(all(questions[q]['answer'] is not None for q in ids),
            'Interpretations cannot replace missing operator answers.')
    require(isinstance(settled['supersedes'], list) and
            all(isinstance(d, str) for d in settled['supersedes']) and
            len(settled['supersedes']) == len(set(settled['supersedes'])),
            'Cite superseded decision IDs explicitly.')
    if settled['basis'] == 'accepted_recommendation':
        require(settled['meaning'] == questions[decision['question_id']]['recommendation'],
                'Accepted recommendations retain the actual recommendation as settled meaning.')


def validate_synthesis(record, questions, require, fields, text):
    synthesis = record.get('synthesis')
    if synthesis is None:
        return ['synthesis']
    require(fields(synthesis, 'reference vision milestone') and text(synthesis['reference']),
            'Provide distinct attributed vision and finite milestone content.')
    current = {d['question_id'] for d in record['decisions'] if d['scope'] == 'current'}
    current.update(q for d in record['decisions'] if d['scope'] == 'current'
                   for q in d.get('settled', {}).get('question_ids', []))
    future_only = {d['question_id'] for d in record['decisions'] if d['scope'] == 'future'} - current
    unresolved = []
    for name, singles, lists in [
        ('vision', 'goal', 'capabilities future_capabilities'),
        ('milestone', 'goal scope definition_of_done',
         'behavior requirements constraints non_goals acceptance testing technical_choices'),
    ]:
        document = synthesis[name]
        require(fields(document, singles + ' ' + lists), 'Retain all specification sections, including finite completion and testing.')
        claims = [document[key] for key in singles.split()]
        for key in lists.split():
            values = document[key]
            require(isinstance(values, list) and (bool(values) or key == 'future_capabilities'),
                    'Specification sections require substantive claims.')
            claims.extend(values)
        for claim in claims:
            require(fields(claim, 'text question_ids') and text(claim['text']),
                    'Each synthesized claim needs text and supporting questions.')
            ids = claim['question_ids']
            require(isinstance(ids, list) and bool(ids) and
                    all(isinstance(q, str) and q in questions for q in ids) and len(ids) == len(set(ids)),
                    'Synthesized claims must cite recorded questions.')
            require(name != 'milestone' or not future_only.intersection(ids),
                    'Future-only capabilities cannot become current milestone content.')
            unresolved.extend(q for q in ids if questions[q]['answer'] is None)
    return unresolved


def active_decisions(decisions):
    superseded = {identifier for d in decisions for identifier in d.get('settled', {}).get('supersedes', [])}
    return [d for d in decisions if d['id'] not in superseded]


def validate_reconciliation(decisions, require):
    earlier = {}
    for decision in decisions:
        settled = decision.get('settled', {})
        for identifier in settled.get('supersedes', []):
            require(identifier in earlier and earlier[identifier]['scope'] == decision['scope']
                    and earlier[identifier]['question_id'] in settled['question_ids']
                    and settled['status'] in {'resolved', 'inapplicable'},
                    'Reconciliation must cite earlier same-scope decisions and their operator sources.')
        earlier[decision['id']] = decision


def meanings(decisions):
    return [{
        'id': d['id'], 'category': d['category'], 'scope': d['scope'],
        **d.get('settled', {'status': 'resolved', 'meaning': d['value'],
                          'basis': 'operator_answer', 'question_ids': [d['question_id']],
                          'rationale': 'Legacy direct operator answer.', 'supersedes': []}),
    } for d in active_decisions(decisions)]
