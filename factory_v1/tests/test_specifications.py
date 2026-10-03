"""Conversation/spec lifecycle fixtures, not live publication evidence."""
import copy
import json
import unittest

from factory_v1.tests import test_repositories


class SpecificationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_repositories.RepositoryLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.register()
        self.assertEqual(self.fixture.onboard().returncode, 0)
        self.root = self.fixture.root
        self.cli = self.fixture.cli
        self.request = {
            'origin': copy.deepcopy(self.fixture.request['origin']),
            'expected_revision': 0,
            'questions': [{'id': 'Q1', 'question': 'Which language?',
                           'challenge': 'Is the ecosystem suitable?',
                           'recommendation': 'Use Python', 'answer': None}],
            'decisions': [],
            'judgment': {'sufficient': False, 'rationale': 'Need operator input',
                         'reference': 'agent-turn-1'},
        }

    def interview(self, request=None):
        path = self.root / 'interview.json'
        path.write_text(json.dumps(self.request if request is None else request))
        return self.cli('interview', '--project', 'product', '--iteration', 'm1',
                        '--request', str(path))

    def inspect(self):
        result = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_untrusted_or_ambiguous_interview_is_refused_without_state_change(self):
        before = self.inspect()
        unsafe = []
        for field, value in [('origin', dict(self.request['origin'], thread_id='999')),
                             ('memory', 'unrelated context'), ('expected_revision', True),
                             ('judgment', {'sufficient': 'yes'}), ('questions', [])]:
            record = copy.deepcopy(self.request)
            record[field] = value
            unsafe.append(record)
        record = copy.deepcopy(self.request)
        record['questions'][0]['answer'] = {'operator_id': '99', 'reference': 'message-2', 'text': 'Python'}
        unsafe.append(record)
        record = copy.deepcopy(self.request)
        record['questions'].append(copy.deepcopy(record['questions'][0]))
        unsafe.append(record)
        for record in unsafe:
            with self.subTest(record=record):
                result = self.interview(record)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_interview')
                self.assertEqual(self.inspect(), before)

    def test_revisions_keep_actual_answers_and_refuse_stale_overwrites(self):
        first = self.interview()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(self.interview().stdout), json.loads(first.stdout))
        record = copy.deepcopy(self.request)
        record['expected_revision'] = 1
        record['questions'][0]['answer'] = {'operator_id': '42', 'reference': 'message-2', 'text': 'Use Rust, not Python'}
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['revision'], 2)
        self.assertEqual(item['interview']['history'][0]['record'], self.request)
        self.assertEqual(item['interview']['record'], record)
        stale = self.interview(dict(self.request, judgment=dict(self.request['judgment'], reference='different-turn')))
        self.assertEqual(stale.returncode, 2)
        self.assertEqual(json.loads(stale.stderr)['error'], 'interview_conflict')
        self.assertEqual(self.inspect(), item)

    def resolved_record(self):
        record = copy.deepcopy(self.request)
        values = {
            'vision': 'Personal notes now; team sharing in a later iteration',
            'milestone': 'Local note create/read only in this iteration',
            'programming_language': 'Rust, not the recommended Python',
            'user_facing_language': 'French', 'stack': 'Standard-library file storage',
            'requirements': 'Create and read a local note', 'constraints': 'No network or spending',
            'non_goals': 'No team sharing this milestone',
            'acceptance': 'Creating a note then reading it returns the same text',
            'testing': 'CLI round-trip and invalid-path refusal tests',
        }
        record['questions'] = []
        record['decisions'] = []
        for index, (category, value) in enumerate(values.items()):
            question_id = f'Q{index + 1}'
            record['questions'].append({'id': question_id, 'question': f'Choose {category}',
                                        'challenge': f'What tradeoff limits {category}?',
                                        'recommendation': 'Prefer a small local slice',
                                        'answer': {'operator_id': '42', 'reference': f'message-{index + 2}', 'text': value}})
            record['decisions'].append({'id': f'D{index + 1}', 'category': category, 'value': value,
                                        'question_id': question_id, 'scope': 'future' if category == 'vision' else 'current'})
        for decision in record['decisions']:
            decision['settled'] = {
                'status': 'resolved', 'meaning': decision['value'], 'basis': 'operator_answer',
                'question_ids': [decision['question_id']], 'rationale': 'Direct operator choice.',
                'supersedes': [],
            }
        record['judgment'] = {'sufficient': True, 'rationale': 'Finite behavior and choices resolved', 'reference': 'agent-turn-20'}
        record['synthesis'] = self.synthesis(record)
        return record

    def test_sufficient_interview_prepares_separate_traceable_versioned_documents(self):
        record = self.resolved_record()
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['status'], 'sufficient')
        spec = item['specification']
        self.assertEqual(spec['status'], 'publication_blocked')
        self.assertEqual(spec['interview_revision'], 1)
        self.assertEqual(len(spec['revision']), 64)
        docs = spec['documents']
        self.assertEqual(set(docs), {'vision.json', 'milestone.json', 'decisions.json'})
        self.assertEqual(json.loads(docs['decisions.json']), record)
        vision = json.loads(docs['vision.json'])
        milestone = json.loads(docs['milestone.json'])
        self.assertEqual(vision['decisions'], record['decisions'])
        self.assertEqual(milestone['decisions'], [d for d in record['decisions'] if d['scope'] == 'current'])
        self.assertNotIn('Personal notes now;', docs['milestone.json'])
        self.assertIn('Rust, not the recommended Python', docs['milestone.json'])
        self.assertTrue(spec['docs_path'].startswith('docs/factory/'))
        self.assertIn(spec['revision'], spec['docs_path'])
        self.assertNotIn('handoff', item)
        self.assertFalse(item['execution_allowed'])
        self.assertEqual(self.inspect(), item)

    def test_agent_judgment_cannot_replace_missing_operator_choices(self):
        for missing in ['answer', 'programming_language', 'testing', 'milestone']:
            record = self.resolved_record()
            if missing == 'answer':
                record['questions'][0]['answer'] = None
                record['decisions'] = record['decisions'][1:]
            else:
                record['decisions'] = [d for d in record['decisions'] if d['category'] != missing]
            with self.subTest(missing=missing):
                record['expected_revision'] = self.inspect().get('interview', {}).get('revision', 0)
                result = self.interview(record)
                self.assertEqual(result.returncode, 0, result.stderr)
                item = json.loads(result.stdout)
                self.assertEqual(item['interview']['status'], 'pending')
                self.assertIn(missing if missing != 'answer' else 'Q1', item['interview']['unresolved'])
                self.assertNotIn('specification', item)
                self.assertNotIn('handoff', item)

    def test_decisions_cannot_invent_or_misattribute_operator_answers(self):
        before = self.inspect()
        for change in [{'value': 'Python'}, {'question_id': 'nonexistent'}, {'scope': 'executable'},
                       {'id': 'D1'}, {'category': 'arbitrary'}]:
            record = self.resolved_record()
            record['decisions'][2].update(change)
            with self.subTest(change=change):
                result = self.interview(record)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_interview')
                self.assertEqual(self.inspect(), before)

    def skill_configuration(self):
        names = ['grill-me', 'to-spec', 'to-tickets', 'implement', 'simplify', 'code-review', 'interview-trace']
        catalog = []
        import hashlib
        for name in names:
            content = f'Fresh fixture skill: {name}\n'.encode()
            path = self.root / f'{name}.md'
            path.write_bytes(content)
            catalog.append({'name': name, 'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
                            'dependencies': ['interview-trace'] if name == 'to-spec' else [],
                            'implicit_loops': ['routine_spec_approval', 'nested_duplicate_review'] if name == 'to-spec' else []})
        return {'catalog': catalog, 'stages': dict(zip(
            ['grilling', 'specification', 'tickets', 'implementation', 'simplification', 'review'], names[:6]))}

    def test_required_skill_closure_is_pinned_with_autonomous_stage_contracts(self):
        record = self.resolved_record()
        record['skills'] = self.skill_configuration()
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        resolution = item['interview']['skills']
        self.assertIn('interview-trace', resolution['resolved'])
        self.assertEqual(resolution['resolved']['to-spec']['sha256'], record['skills']['catalog'][1]['sha256'])
        contract = resolution['contracts']['specification']
        self.assertFalse(contract['routine_approval'])
        self.assertEqual(contract['adapted_loops'], ['routine_spec_approval', 'nested_duplicate_review'])
        self.assertEqual(resolution['contracts']['review']['gate'], 'independent_requirement_review')
        self.assertFalse(resolution['contracts']['tickets']['routine_approval'])
        self.assertEqual(self.inspect(), item)

    def test_missing_ambiguous_cyclic_or_changed_skills_fail_closed(self):
        before = self.inspect()
        for defect in ['missing', 'ambiguous', 'cycle', 'changed', 'gate', 'stage', 'malformed']:
            record = self.resolved_record()
            skills = self.skill_configuration()
            record['skills'] = skills
            if defect == 'missing':
                skills['catalog'][1]['dependencies'] = ['absent']
            elif defect == 'ambiguous':
                skills['catalog'].append(copy.deepcopy(skills['catalog'][1]))
            elif defect == 'cycle':
                skills['catalog'][-1]['dependencies'] = ['to-spec']
            elif defect == 'changed':
                (self.root / 'to-spec.md').write_text('changed skill')
            elif defect == 'gate':
                skills['catalog'][1]['implicit_loops'] = ['required_security_review']
            elif defect == 'stage':
                skills['stages'].pop('review')
            else:
                skills['catalog'] = [None]
            with self.subTest(defect=defect):
                result = self.interview(record)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'skill_blocked')
                self.assertEqual(self.inspect(), before)

    def test_export_is_an_exact_publication_request_not_delivery_evidence(self):
        result = self.interview(self.resolved_record())
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.inspect()
        spec = before['specification']
        exported = self.cli('export-spec', '--project', 'product', '--iteration', 'm1', '--revision', spec['revision'])
        self.assertEqual(exported.returncode, 0, exported.stderr)
        package = json.loads(exported.stdout)
        self.assertEqual(package['repository'], 'example/product')
        self.assertEqual(package['revision'], spec['revision'])
        self.assertEqual(package['files'], {spec['docs_path'] + '/' + name: content for name, content in spec['documents'].items()})
        self.assertIn('reviewed controller', package['capability_request'])
        self.assertEqual(self.inspect(), before)
        self.assertFalse(any(call[0] == 'POST' for call in self.fixture.calls))

    def publication_fixture(self, spec, commit='a' * 40):
        import base64
        import hashlib
        parent = self.fixture.server.RequestHandlerClass
        case = self
        case.published = {}
        for name, content in spec['documents'].items():
            path = spec['docs_path'] + '/' + name
            raw = content.encode()
            case.published['/repos/example/product/contents/' + path + '?ref=' + commit] = {
                'path': path, 'type': 'file', 'encoding': 'base64',
                'content': base64.b64encode(raw).decode(),
                'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
        case.published['/repos/example/product/git/commits/' + commit] = {'sha': commit}

        class Handler(parent):
            def do_GET(self):
                if self.path in case.published:
                    case.fixture.calls.append(('GET', self.path))
                    self.reply(200, case.published[self.path])
                else:
                    super().do_GET()

        self.fixture.server.RequestHandlerClass = Handler

    def verify(self, spec, commit='a' * 40, base=None):
        return self.cli('verify-spec', '--project', 'product', '--iteration', 'm1',
                        '--revision', spec['revision'], '--commit', commit,
                        '--api-base', self.fixture.base if base is None else base)

    def test_exact_immutable_document_readback_automatically_hands_off_current_milestone(self):
        record = self.resolved_record()
        record['skills'] = self.skill_configuration()
        self.assertEqual(self.interview(record).returncode, 0)
        spec = self.inspect()['specification']
        self.publication_fixture(spec)
        result = self.verify(spec)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['specification']['status'], 'verified')
        self.assertEqual(item['specification']['publication']['commit'], 'a' * 40)
        self.assertEqual(item['stage'], 'ticket_synthesis_ready')
        handoff = item['handoff']
        self.assertEqual(handoff['stage'], 'ticket_synthesis')
        self.assertEqual(handoff['spec_reference'], {'repository': 'example/product', 'commit': 'a' * 40,
                                                   'path': spec['docs_path'] + '/milestone.json', 'revision': spec['revision']})
        self.assertEqual(handoff['decision_ids'], [d['id'] for d in record['decisions'] if d['scope'] == 'current'])
        self.assertFalse(handoff['batch_approval_required'])
        self.assertFalse(item['execution_allowed'])
        self.assertEqual(json.loads(self.verify(spec).stdout), item)
        self.assertEqual(self.inspect(), item)
        self.assertFalse(any(call[0] == 'POST' for call in self.fixture.calls))

    def test_publication_mismatch_never_admits_a_handoff(self):
        record = self.resolved_record()
        record['skills'] = self.skill_configuration()
        self.assertEqual(self.interview(record).returncode, 0)
        before = self.inspect()
        spec = before['specification']
        for field, value in [('path', 'docs/wrong.json'), ('content', 'aW52ZW50ZWQ='),
                             ('sha', 'b' * 40), ('type', 'symlink'), ('encoding', 'none')]:
            self.publication_fixture(spec)
            key = next(path for path in self.published if '/contents/' in path)
            self.published[key][field] = value
            result = self.verify(spec)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)['error'], 'publication_mismatch')
            self.assertEqual(self.inspect(), before)

    def test_publication_cannot_change_verified_repository_identity(self):
        record = self.resolved_record()
        record['skills'] = self.skill_configuration()
        self.assertEqual(self.interview(record).returncode, 0)
        before = self.inspect()
        spec = before['specification']
        self.publication_fixture(spec)
        self.fixture.repo['id'] = 999
        result = self.verify(spec)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.assertEqual(self.inspect(), before)

    def test_missing_or_changed_skill_files_prevent_publication_handoff(self):
        self.assertEqual(self.interview(self.resolved_record()).returncode, 0)
        spec = self.inspect()['specification']
        self.publication_fixture(spec)
        result = self.verify(spec)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'skill_blocked')
        record = self.resolved_record()
        record['expected_revision'] = 1
        record['skills'] = self.skill_configuration()
        self.assertEqual(self.interview(record).returncode, 0)
        spec = self.inspect()['specification']
        self.publication_fixture(spec)
        (self.root / 'to-tickets.md').write_text('changed after interview')
        before = self.inspect()
        result = self.verify(spec)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'skill_blocked')
        self.assertEqual(self.inspect(), before)

    def test_new_interview_input_invalidates_handoff_but_retains_old_pinned_documents(self):
        record = self.resolved_record()
        record['skills'] = self.skill_configuration()
        self.assertEqual(self.interview(record).returncode, 0)
        old_spec = self.inspect()['specification']
        self.publication_fixture(old_spec)
        self.assertEqual(self.verify(old_spec).returncode, 0)
        old_spec = self.inspect()['specification']
        pending = copy.deepcopy(self.request)
        pending['expected_revision'] = 1
        result = self.interview(pending)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertNotIn('handoff', item)
        self.assertNotIn('specification', item)
        self.assertEqual(item['specification_history'][0], old_spec)
        self.assertEqual(item['stage'], 'interview_pending')
        self.assertFalse(item['execution_allowed'])
        stale = self.verify(old_spec)
        self.assertEqual(stale.returncode, 2)
        self.assertEqual(json.loads(stale.stderr)['error'], 'specification_conflict')
        self.assertEqual(self.inspect(), item)

    def test_other_operator_cannot_submit_export_or_verify_this_interview(self):
        import os
        import subprocess
        import sys
        self.assertEqual(self.interview(self.resolved_record()).returncode, 0)
        before = self.inspect()
        spec = before['specification']
        path = self.root / 'wrong-operator.json'
        path.write_text(json.dumps(dict(self.request, expected_revision=1)))
        commands = [
            ['interview', '--request', str(path)],
            ['export-spec', '--revision', spec['revision']],
            ['verify-spec', '--revision', spec['revision'], '--commit', 'a' * 40, '--api-base', self.fixture.base],
        ]
        for command in commands:
            result = subprocess.run([sys.executable, '-m', 'factory_v1', '--state', str(self.fixture.state),
                                     '--operator-id', '99', *command, '--project', 'product', '--iteration', 'm1'],
                                    text=True, capture_output=True, timeout=15,
                                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)['error'], 'approval_required')
            self.assertEqual(self.inspect(), before)

    def test_interview_requires_verified_onboarding_before_accepting_product_context(self):
        self.fixture.state = self.root / 'not-onboarded.sqlite'
        self.fixture.register()
        before = self.inspect()
        result = self.interview()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'onboarding_required')
        self.assertEqual(self.inspect(), before)

    def test_conflicting_current_language_choices_remain_pending(self):
        record = self.resolved_record()
        record['questions'].append({
            'id': 'Q-conflict', 'question': 'Which implementation language is settled?',
            'challenge': 'This conflicts with the earlier Rust answer.',
            'recommendation': 'Resolve one implementation language explicitly.',
            'answer': {'operator_id': '42', 'reference': 'message-conflict', 'text': 'Python'},
        })
        record['decisions'].append({
            'id': 'D-conflict', 'category': 'programming_language', 'value': 'Python',
            'question_id': 'Q-conflict', 'scope': 'current',
        })
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['status'], 'pending')
        self.assertIn('programming_language', item['interview']['unresolved'])
        self.assertEqual(item['interview']['record'], record)
        self.assertNotIn('specification', item)
        self.assertNotIn('handoff', item)
        self.assertEqual(self.inspect(), item)

    def test_accepted_recommendation_has_separate_settled_meaning(self):
        record = self.resolved_record()
        question = record['questions'][2]
        question['recommendation'] = 'Use Python for this local tool'
        question['answer']['text'] = 'I accept that recommendation.'
        decision = record['decisions'][2]
        decision['value'] = question['answer']['text']
        decision['settled'] = {
            'status': 'resolved', 'meaning': question['recommendation'],
            'basis': 'accepted_recommendation', 'question_ids': [question['id']],
            'rationale': 'The operator explicitly accepted this recommendation.',
            'supersedes': [],
        }
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['status'], 'sufficient')
        milestone = json.loads(item['specification']['documents']['milestone.json'])
        settled = next(d for d in milestone['settled_decisions'] if d['id'] == decision['id'])
        self.assertEqual(settled['meaning'], question['recommendation'])
        self.assertEqual(json.loads(item['specification']['documents']['decisions.json']), record)
        self.assertEqual(self.inspect(), item)

    def test_unsettled_current_choices_cannot_be_admitted_by_category_presence(self):
        for status in [None, 'unresolved', 'deferred']:
            record = self.resolved_record()
            decision = record['decisions'][3]
            record['questions'][3]['answer']['text'] = 'I have not decided; ask me later.'
            decision['value'] = record['questions'][3]['answer']['text']
            if status is None:
                decision.pop('settled')
            else:
                decision['settled'].update(status=status, meaning='No current language selected',
                                            rationale='Operator has not supplied the current choice.')
            record['expected_revision'] = self.inspect().get('interview', {}).get('revision', 0)
            with self.subTest(status=status):
                result = self.interview(record)
                self.assertEqual(result.returncode, 0, result.stderr)
                item = json.loads(result.stdout)
                self.assertEqual(item['interview']['status'], 'pending')
                self.assertIn('user_facing_language', item['interview']['unresolved'])
                self.assertNotIn('specification', item)
                self.assertNotIn('handoff', item)
                self.assertEqual(item['interview']['record'], record)

    def synthesis(self, record):
        def claim(value, question_ids):
            return {'text': value, 'question_ids': question_ids}
        return {
            'reference': 'agent-synthesis-21',
            'vision': {
                'goal': claim('A personal note tool with possible later collaboration.', ['Q1']),
                'capabilities': [claim('Create and read notes locally.', ['Q6'])],
                'future_capabilities': [claim('Team sharing waits for another interview.', ['Q1'])],
            },
            'milestone': {
                'goal': claim('Deliver local note persistence through the CLI.', ['Q2']),
                'scope': claim('This iteration delivers create/read only; no recurring feature batch.', ['Q2']),
                'definition_of_done': claim('Create then read returns the exact note; refusal tests pass.', ['Q9', 'Q10']),
                **{section: [claim(record['decisions'][index]['settled']['meaning'], [f'Q{index + 1}'])]
                   for section, index in [('behavior', 5), ('requirements', 5), ('constraints', 6),
                                          ('non_goals', 7), ('acceptance', 8), ('testing', 9)]},
                'technical_choices': [claim(record['decisions'][i]['settled']['meaning'], [f'Q{i + 1}'])
                                      for i in [2, 3, 4]],
            },
        }

    def test_export_contains_synthesized_finite_content_not_transcript_lists(self):
        record = self.resolved_record()
        record['synthesis'] = self.synthesis(record)
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        exported = self.cli('export-spec', '--project', 'product', '--iteration', 'm1',
                            '--revision', item['specification']['revision'])
        self.assertEqual(exported.returncode, 0, exported.stderr)
        files = json.loads(exported.stdout)['files']
        vision = json.loads(next(value for path, value in files.items() if path.endswith('/vision.json')))
        milestone = json.loads(next(value for path, value in files.items() if path.endswith('/milestone.json')))
        self.assertEqual(vision['content'], record['synthesis']['vision'])
        self.assertEqual(milestone['content'], record['synthesis']['milestone'])
        self.assertIn('create/read only', milestone['content']['scope']['text'])
        self.assertEqual(milestone['content']['acceptance'][0]['question_ids'], ['Q9'])
        self.assertNotIn('future_capabilities', milestone['content'])
        self.assertFalse(item['execution_allowed'])

    def test_synthesis_requires_finite_sections_and_valid_source_attribution(self):
        before = self.inspect()
        for defect in ['missing_done', 'unknown_question', 'future_only', 'empty_acceptance', 'extra_memory']:
            record = self.resolved_record()
            milestone = record['synthesis']['milestone']
            if defect == 'missing_done':
                milestone.pop('definition_of_done')
            elif defect == 'unknown_question':
                milestone['scope']['question_ids'] = ['Q-absent']
            elif defect == 'future_only':
                milestone['scope']['question_ids'] = ['Q1']
            elif defect == 'empty_acceptance':
                milestone['acceptance'] = []
            else:
                record['synthesis']['memory'] = 'unrelated context'
            with self.subTest(defect=defect):
                result = self.interview(record)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_interview')
                self.assertEqual(self.inspect(), before)
        record = self.resolved_record()
        record.pop('synthesis')
        result = self.interview(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['status'], 'pending')
        self.assertIn('synthesis', item['interview']['unresolved'])
        self.assertNotIn('specification', item)

    def test_later_clarification_reconciles_choice_without_rewriting_provenance(self):
        record = self.resolved_record()
        old = copy.deepcopy(record['decisions'][2])
        record['questions'].append({
            'id': 'Q-later', 'question': 'Resolve the language change?',
            'challenge': 'Rust and Python cannot both be the current choice.',
            'recommendation': 'Choose one.',
            'answer': {'operator_id': '42', 'reference': 'message-later', 'text': 'Use Python instead.'},
        })
        record['decisions'].append({
            'id': 'D-later', 'category': 'programming_language', 'value': 'Use Python instead.',
            'question_id': 'Q-later', 'scope': 'current',
            'settled': {'status': 'resolved', 'meaning': 'Python', 'basis': 'clarification',
                        'question_ids': ['Q-later', 'Q3'], 'rationale': 'Later operator answer replaces Rust.',
                        'supersedes': ['D3']},
        })
        record['synthesis']['milestone']['technical_choices'][0] = {
            'text': 'Python', 'question_ids': ['Q-later', 'Q3']}
        self.assertEqual(self.interview(record).returncode, 0)
        item = self.inspect()
        self.assertEqual(item['interview']['status'], 'sufficient')
        self.assertEqual(item['interview']['record']['decisions'][2], old)
        milestone = json.loads(item['specification']['documents']['milestone.json'])
        self.assertNotIn('D3', [d['id'] for d in milestone['settled_decisions']])
        self.assertIn('D-later', [d['id'] for d in milestone['settled_decisions']])
        record['skills'] = self.skill_configuration()
        record['expected_revision'] = 1
        self.assertEqual(self.interview(record).returncode, 0)
        spec = self.inspect()['specification']
        self.publication_fixture(spec)
        self.assertEqual(self.verify(spec).returncode, 0)
        self.assertNotIn('D3', self.inspect()['handoff']['decision_ids'])

    def test_unanswered_conversation_is_durably_pending(self):
        result = self.interview()
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['interview']['status'], 'pending')
        self.assertEqual(item['interview']['revision'], 1)
        self.assertEqual(item['interview']['record'], self.request)
        self.assertEqual(item['stage'], 'interview_pending')
        self.assertFalse(item['execution_allowed'])
        self.assertNotIn('specification', item)
        self.assertEqual(self.inspect(), item)


if __name__ == '__main__':
    unittest.main()
