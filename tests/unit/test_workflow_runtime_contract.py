"""A workflow invocation owns its context, evidence and delivery across steps."""
from __future__ import annotations

import copy

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import artifacts, conversations, projects
from agent_friday.services import scheduler, task_journal, task_resume
try:
    from agent_friday.services import workflow_outcomes as outcomes
except ImportError:  # The behavioral regression subset also runs on the old baseline.
    outcomes = None


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ag, 'TASKS', {})
    monkeypatch.setattr(ag, 'TASK_THREADS', {})
    monkeypatch.setattr(ag, 'WORKFLOWS_DIR', tmp_path / 'workflows')
    monkeypatch.setattr(task_journal, 'BASE_DIR_OVERRIDE', tmp_path / 'tasks')
    task_journal.reset_for_tests()
    monkeypatch.setattr(ag, '_log_context', lambda *a, **kw: None)
    monkeypatch.setattr(ag, '_load_settings', lambda: {})
    owners = {cid: {'id': cid, 'project': 'project-research'} for cid in
              ('conv-research', 'conv-a', 'conv-b', 'conv-owned')}
    monkeypatch.setattr(conversations, 'load', lambda cid: owners.get(cid))
    monkeypatch.setattr(projects, 'load', lambda pid: {'id': pid} if pid == 'project-research' else None)

    def create(**kw):
        cid = 'conv-created-' + str(len(owners))
        owners[cid] = {'id': cid, 'project': None}
        return owners[cid]
    monkeypatch.setattr(conversations, 'create', create)
    monkeypatch.setattr(conversations, 'patch', lambda cid, **kw: owners[cid].update(kw))
    yield
    task_journal.reset_for_tests()


def definition(**fields):
    return dict({'name': 'Research brief', 'conversation_id': 'conv-research',
                 'project_id': 'project-research', 'inputs': ['Read the attached source'],
                 'success_criteria': 'Explain limitations.',
                 'output': {'kind': 'artifact', 'title': 'Research brief'},
                 'notify': 'on_change',
                 'steps': [{'name': 'Gather', 'prompt': 'Gather source material', 'retries': 2},
                           {'name': 'Write', 'prompt': 'Write the research brief', 'with_context': True}]},
                **fields)


@pytest.fixture
def fake_spawn(monkeypatch):
    calls = []

    def spawn(name=None, prompt=None, **kw):
        kw.update(name=name, prompt=prompt)
        calls.append(copy.deepcopy(kw))
        tid = kw.get('task_id') or 'task-' + str(len(calls))
        if tid not in ag.TASKS:
            rec = dict(kw)
            rec.update(rec.pop('workflow_context', {}) or {})
            rec.update(task_id=tid, created=len(calls), status='queued', result='', log=[])
            ag.TASKS[tid] = rec
        return tid
    monkeypatch.setattr(ag, '_spawn_task', spawn)
    return calls


def test_interleaved_runs_keep_definition_and_owner_through_retry(fake_spawn):
    saved = ag.save_workflow_chain(definition(conversation_id='conv-a'))
    a = ag.run_workflow_chain(saved['slug'], conversation_id='conv-a', schedule_id='schedule-a')
    ag.save_workflow_chain(definition(conversation_id='conv-b'))
    b = ag.run_workflow_chain(saved['slug'], conversation_id='conv-b')
    run_a, run_b = ag.TASKS[a]['run_id'], ag.TASKS[b]['run_id']
    ag.save_workflow_chain(definition(steps=[{'name': 'Changed', 'prompt': 'Use a different source'}]))
    ag.TASKS[a].update(status='complete', result='Source findings from invocation A.')
    second = ag._advance_task_chain(a, ag.TASKS[a]['result'])
    ag.TASKS[second]['status'] = 'failed'
    retry = ag._retry_chain_step(second, 'The output store was unavailable')
    rec = ag.TASKS[retry]
    assert rec['run_id'] == run_a != run_b
    assert rec['conversation_id'] == 'conv-a' and rec['schedule_id'] == 'schedule-a'
    assert rec['project_id'] == 'project-research' and rec['workflow_revision'] == 1
    assert rec['chain_retry'] == 1
    assert 'Source findings from invocation A.' in rec['prompt']
    assert 'different source' not in rec['prompt']
    state = ag.chain_run_status(saved['slug'], run_id=run_a)
    assert len(state['steps']) == 2 and state['steps'][1]['task_id'] == retry
    assert ag.chain_run_status(saved['slug'], run_id=run_b)['steps'][0]['task_id'] == b


def test_one_step_runs_do_not_merge_and_unverified_is_not_completed(fake_spawn):
    saved = ag.save_workflow_chain(definition(steps=[{'name': 'Write', 'prompt': 'Write report'}]))
    one = ag.run_workflow_chain(saved['slug'])
    two = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[one]['status'] = 'complete'
    ag.TASKS[two]['status'] = 'completed_unverified'
    assert ag.chain_run_status(saved['slug'])['state'] == 'completed_unverified'
    assert ag.chain_run_status(saved['slug'], ag.TASKS[one]['run_id'])['state'] == 'completed'
    assert ag.chain_run_status(saved['slug'], 'missing-invocation') is None


def test_duplicate_advance_and_retry_have_identical_descendant_identity(fake_spawn):
    saved = ag.save_workflow_chain(definition())
    first = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[first]['status'] = 'complete'
    a = ag._advance_task_chain(first, 'Collected trustworthy source findings.')
    b = ag._advance_task_chain(first, 'Collected trustworthy source findings.')
    assert a == b and len(ag.TASKS) == 2
    ag.TASKS[a]['status'] = 'failed'
    assert ag._retry_chain_step(a, 'temporary error') == ag._retry_chain_step(a, 'temporary error')
    assert len(ag.TASKS) == 3


def test_one_off_followup_preserves_conversation_and_grant(fake_spawn):
    ag.TASKS['source'] = {'task_id': 'source', 'name': 'Prepare', 'status': 'complete',
        'conversation_id': 'conv-source', 'schedule_id': 'schedule-source',
        'model': 'local-test', 'pin_to_seat': True,
        'on_complete': {'spawn': 'Review', 'prompt': 'Review the draft'}}
    child = ag._advance_task_chain('source', 'The draft is ready for review.')
    assert ag.TASKS[child]['conversation_id'] == 'conv-source'
    assert ag.TASKS[child]['schedule_id'] == 'schedule-source'
    assert ag.TASKS[child]['pin_to_seat'] is True
    assert ag._advance_task_chain('source', 'The draft is ready for review.') == child


def test_definition_revision_is_monotonic_and_existing_options_survive():
    first = ag.save_workflow_chain(definition(revision=700))
    second = ag.save_workflow_chain({'name': first['name'], 'steps': first['steps'], 'revision': 1})
    assert (first['revision'], second['revision']) == (1, 2)
    assert second['output'] == first['output'] and second['notify'] == 'on_change'
    assert second['steps'][0]['retries'] == 2


def test_first_legacy_edit_preserves_original_history_as_version_one():
    import json
    from agent_friday.services import workflow_operations
    legacy = definition()
    legacy['slug'] = ag._chain_slug(legacy['name'])
    path = ag._workflows_dir() / (legacy['slug'] + '.json')
    path.write_text(json.dumps(legacy), encoding='utf-8')
    workflow_operations.remember_definition(legacy)
    changed = ag.save_workflow_chain(dict(legacy, description='Clarified source coverage'))
    workflow_operations.remember_definition(changed)
    assert changed['revision'] == 2
    assert [row['revision'] for row in workflow_operations.revisions(legacy['slug'])] == [2, 1]
    original = json.loads((workflow_operations._history_dir(legacy['slug']) / 'v1.json').read_text(encoding='utf-8'))
    assert original['definition'] == legacy


def test_substantive_reply_checks_structure_without_claiming_quality():
    result = outcomes.verify({'output': {'kind': 'reply'}, 'success_criteria': 'Cite reliable sources'},
                             'The evidence supports the following detailed findings.', [])
    assert result['verified'] is True
    assert result['scope'] == 'deliverable_structure'
    criterion = next(c for c in result['checks'] if c['name'] == 'success_criteria')
    assert criterion['status'] == 'not_automatically_checked'
    assert not outcomes.verify({}, "I'll research that for you now.", [])['verified']


def test_search_or_refused_write_does_not_verify_expected_artifact():
    contract = {'output': {'kind': 'artifact'}}
    trace = [{'name': 'search_web', 'result': 'Found useful source results.'},
             {'name': 'artifact_put', 'result': {'status': 'error', 'artifact_id': 'art-1', 'version': 1}}]
    assert not outcomes.verify(contract, 'The report is complete.', trace, conversation_id='conv-a')['verified']


def test_artifact_evidence_is_bound_to_version_conversation_freshness_and_hash(monkeypatch):
    body = 'A research brief with supported findings and limitations.'
    record = {'id': 'art-1', 'conversation_id': 'conv-a', 'ts': 200,
              'title': 'Brief', 'content': body, 'sha256': artifacts._sha(body), 'version': 2}
    reads = []
    monkeypatch.setattr(artifacts, 'get', lambda cid, aid, version=None:
                        reads.append((cid, aid, version)) or record)
    trace = [{'name': 'artifact_put', 'input': {},
              'result': {'status': 'ok', 'artifact_id': 'art-1', 'version': 2}}]
    contract = {'output': {'kind': 'artifact', 'title': 'Brief'}}
    assert outcomes.verify(contract, '', trace, conversation_id='conv-a', started_at=100)['verified']
    assert reads[-1] == ('conv-a', 'art-1', 2)
    record['content'] = 'Changed without matching hash'
    assert not outcomes.verify(contract, '', trace, conversation_id='conv-a', started_at=100)['verified']
    record['content'] = body
    assert not outcomes.verify(contract, '', trace, conversation_id='conv-b', started_at=100)['verified']
    assert not outcomes.verify(contract, '', trace, conversation_id='conv-a', started_at=300)['verified']


def test_file_verification_never_reads_unwritten_or_denied_paths(tmp_path):
    reads = []
    path = str(tmp_path / 'brief.md')
    contract = {'output': {'kind': 'file', 'path': path}}
    reader = lambda p: reads.append(p) or '[VAULT ACCESS DENIED]'
    assert not outcomes.verify(contract, 'I wrote the report.', [], file_reader=reader)['verified']
    assert reads == []
    trace = [{'name': 'write_file', 'input': {'path': path}, 'result': 'Wrote 45 chars'}]
    assert not outcomes.verify(contract, '', trace, file_reader=reader)['verified']
    assert len(reads) == 1
    ok = outcomes.verify(contract, '', trace, file_reader=lambda p: 'The saved brief contains evidence and limitations.')
    assert ok['verified'] and ok['outputs'][0]['evidence'] == 'governed_readback'


def test_code_change_requires_matching_durable_commit_receipt():
    sha = 'a' * 40
    trace = [{'name': 'codebase_edit', 'result': {'status': 'ok', 'codebase': 'cb-a', 'step': {'sha': sha}}}]
    contract = {'output': {'kind': 'code'}}
    assert not outcomes.verify(contract, '', trace, code_reader=lambda *a: None)['verified']
    out = outcomes.verify(contract, '', trace, code_reader=lambda *a:
                          {'commit': sha, 'files': [{'path': 'app.js'}], 'tests': None})
    assert out['verified']
    assert any(c['name'] == 'code_review' and c['status'] == 'not_automatically_checked' for c in out['checks'])


def test_completion_delivery_survives_crash_between_append_and_snapshot(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition(steps=[{'name': 'Write', 'prompt': 'Write report'}], notify='on_complete'))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='completed_unverified', result='The draft is ready but references need review.')
    messages, notifications = [], []
    monkeypatch.setattr(conversations, 'messages', lambda cid: messages)
    monkeypatch.setattr(conversations, 'append', lambda cid, msg, **kw: messages.append(msg) or msg)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: notifications.append(kw) or kw)
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: None)
    ag._report_task_completion(task, 'Write', 'completed_unverified', ag.TASKS[task]['result'])
    assert len(messages) == 1 and ag.TASKS[task]['delivery']['status'] == 'delivered'
    assert 'checks are incomplete' in messages[0]['text']
    ag.TASKS[task].pop('delivery')  # task snapshot lost; transcript append survived
    ag._report_task_completion(task, 'Write', 'completed_unverified', ag.TASKS[task]['result'])
    assert len(messages) == 1
    assert all(n['proactive_chat'] is False for n in notifications)


def test_delivery_failure_does_not_turn_verification_into_success(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition(steps=[{'name': 'Write', 'prompt': 'Write report'}]))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='The verified document is available.')
    monkeypatch.setattr(conversations, 'messages', lambda cid: [])
    monkeypatch.setattr(conversations, 'append', lambda *a, **k: None)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: kw)
    ag._report_task_completion(task, 'Write', 'complete', ag.TASKS[task]['result'])
    state = ag.chain_run_status(saved['slug'])
    assert state['state'] == 'completed' and state['delivery']['status'] == 'failed'


def test_checkpoint_retains_run_owner_and_schedule(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition(conversation_id='conv-owned'))
    task = ag.run_workflow_chain(saved['slug'], conversation_id='conv-owned', schedule_id='schedule-owned')
    monkeypatch.setattr(task_resume, 'enabled', lambda: True)
    assert task_resume.checkpoint(task, convo=[{'role': 'user', 'content': 'Continue the brief'}])
    context = task_resume.read(task)['run_context']
    assert context['run_id'] == ag.TASKS[task]['run_id']
    assert context['conversation_id'] == 'conv-owned' and context['schedule_id'] == 'schedule-owned'


def test_scheduler_polls_its_invocation_and_preserves_unverified(monkeypatch):
    started, polled = [], []
    recorded = []
    monkeypatch.setattr(scheduler, '_patch_record', lambda sid, **kw: recorded.append(kw))
    monkeypatch.setattr(ag, 'run_workflow_chain', lambda slug, **kw: started.append(kw) or 'task')
    monkeypatch.setattr(ag, 'chain_run_status', lambda slug, **kw: polled.append(kw) or
                        {'state': 'completed_unverified', 'steps': [{'result_tail': 'Draft needs review'}]})
    result = scheduler._run_workflow({'id': 'schedule-one', '_active_run_id': 'run-one'},
                                     {'ref': 'brief', 'conversation_id': 'conv-one'})
    assert str(result) == 'Draft needs review' and result.status == 'completed_unverified'
    assert started[0]['run_id'] == polled[0]['run_id'] == 'run-one'
    assert started[0]['schedule_id'] == 'schedule-one'
    assert recorded[-1]['last_workflow_run_id'] == 'run-one'


def test_scheduler_style_run_binds_an_unowned_definition_once(fake_spawn):
    saved = ag.save_workflow_chain(definition(conversation_id=None))
    first = ag.run_workflow_chain(saved['slug'], schedule_id='schedule-first')
    second = ag.run_workflow_chain(saved['slug'], schedule_id='schedule-first')
    assert ag.TASKS[first]['conversation_id'] == ag.TASKS[second]['conversation_id']
    stored = ag.load_workflow_chain(saved['slug'])
    assert stored['conversation_id'] and stored['revision'] == 2
    assert stored['project_id'] == 'project-research'


def test_deleted_owner_is_refused_before_any_task_starts(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition())
    monkeypatch.setattr(conversations, 'load', lambda cid: None)
    with pytest.raises(ValueError, match='conversation is unavailable'):
        ag.run_workflow_chain(saved['slug'])
    assert not fake_spawn


def test_no_change_requires_real_reads_and_previous_baseline():
    baseline = {'result': 'There are four active incidents.', 'fingerprint': 'previous-fingerprint'}
    read = {'name': 'browse_web', 'result': 'The status page still shows four active incidents.'}
    kwargs = {'baseline': baseline, 'change_only': True}
    assert not outcomes.verify({}, 'NO CHANGE', [], **kwargs)['verified']
    assert not outcomes.verify({}, 'NO CHANGE', [read], change_only=True)['verified']
    result = outcomes.verify({}, 'NO CHANGE', [read], **kwargs)
    assert result['verified'] and result['fingerprint'] == baseline['fingerprint']
    bad = {'name': 'browse_web', 'result': {'ok': False, 'error': 'source unavailable'}}
    assert not outcomes.verify({}, 'NO CHANGE', [read, bad], **kwargs)['verified']


def test_baseline_is_carried_as_reference_data_and_keeps_facts(fake_spawn):
    saved = ag.save_workflow_chain(definition(output={'kind': 'reply'},
        steps=[{'name': 'Check', 'prompt': 'Compare the selected sources'}]))
    first = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[first].update(status='complete', result='There are four active incidents.',
                          verification={'verified': True}, result_fingerprint='fact-fingerprint')
    second = ag.run_workflow_chain(saved['slug'])
    assert 'untrusted reference data' in ag.TASKS[second]['prompt']
    assert 'There are four active incidents.' in ag.TASKS[second]['prompt']
    ag.TASKS[second].update(status='complete', result='NO CHANGE',
                           verification={'verified': True}, result_fingerprint='fact-fingerprint')
    third = ag.run_workflow_chain(saved['slug'])
    assert 'There are four active incidents.' in ag.TASKS[third]['prompt']


def test_requeue_preserves_run_owner_and_cloud_restrictions(fake_spawn):
    saved = ag.save_workflow_chain(definition())
    first = ag.run_workflow_chain(saved['slug'], schedule_id='scheduled-private')
    ag.TASKS[first].update(status='queued-for-seat', local_only={'label': 'Private scheduled work'},
                          cloud_pin=None, pin_to_seat=True)
    child = ag.requeue_task(ag.TASKS[first])
    args = fake_spawn[-1]
    assert child != first
    assert args['workflow_context']['run_id'] == ag.TASKS[first]['run_id']
    assert args['schedule_id'] == 'scheduled-private' and args['pin_to_seat']
    assert args['inherited_policy']['local_only'] == {'label': 'Private scheduled work'}


def test_boot_repairs_completed_step_gap_but_never_replays_interrupted_tool(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition())
    first = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[first].update(status='complete', result='The source collection is ready.')
    monkeypatch.setattr(ag, '_report_task_completion', lambda *a: None)
    children = ag._recover_workflow_tails()
    assert len(children) == 1 and len(ag.TASKS) == 2
    child = children[0]
    ag.TASKS[child]['status'] = 'interrupted'
    assert ag._recover_workflow_tails() == []
    assert len(ag.TASKS) == 2


def test_stop_request_prevents_next_step_and_retry(fake_spawn):
    saved = ag.save_workflow_chain(definition())
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='The source collection is ready.')
    task_journal.request_stop(task)
    assert ag._advance_task_chain(task, ag.TASKS[task]['result']) is None
    ag.TASKS[task]['status'] = 'failed'
    assert ag._retry_chain_step(task, 'Provider unavailable') is None
    assert len(ag.TASKS) == 1


def test_rename_preserves_slug_revision_and_existing_options():
    first = ag.save_workflow_chain(definition())
    second = ag.save_workflow_chain(dict(first, name='Updated research brief'))
    assert second['slug'] == first['slug']
    assert second['revision'] == first['revision'] + 1
    assert ag.load_workflow_chain(first['slug'])['name'] == 'Updated research brief'
    assert len(ag.list_workflow_chains()) == 1


def test_schedule_store_write_failure_is_visible_and_preserves_old_file(monkeypatch, tmp_path):
    import os
    target = tmp_path / 'schedules.json'
    target.write_text('[{"id": "old"}]', encoding='utf-8')
    monkeypatch.setattr(scheduler, 'SCHEDULES_FILE', target)
    def fail_replace(source, dest):
        raise OSError('simulated unavailable store')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='unavailable store'):
        scheduler._write_store([{'id': 'new'}])
    assert target.read_text(encoding='utf-8') == '[{"id": "old"}]'
    assert list(tmp_path.glob('*.tmp')) == []


def test_schedule_store_publishes_only_after_flushing_complete_json(monkeypatch, tmp_path):
    import json
    import os
    target = tmp_path / 'schedules.json'
    target.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(scheduler, 'SCHEDULES_FILE', target)
    fsync, replace = os.fsync, os.replace
    seen = []
    def observe_fsync(fd):
        seen.append('flushed')
        fsync(fd)
    def observe_replace(source, dest):
        assert seen == ['flushed']
        assert target.read_text(encoding='utf-8') == '[]'
        assert json.loads(source.read_text(encoding='utf-8')) == [{'id': 'new'}]
        replace(source, dest)
    monkeypatch.setattr(os, 'fsync', observe_fsync)
    monkeypatch.setattr(os, 'replace', observe_replace)
    scheduler._write_store([{'id': 'new'}])
    assert json.loads(target.read_text(encoding='utf-8')) == [{'id': 'new'}]


def test_workflow_private_fields_are_sealed_for_non_user_readers(monkeypatch):
    from agent_friday.services import egress_gate
    seen = []
    monkeypatch.setattr(egress_gate, '_gate_text', lambda text, *a:
                        seen.append(text) or '[withheld by test gate]')
    context = {'run_id': 'example-run', 'workflow_definition': {
        'inputs': ['example source reference'], 'success_criteria': 'example quality requirement',
        'output': {'kind': 'file', 'title': 'example output title', 'path': 'example-output.md'}},
        'workflow_baseline': {'result': 'example prior findings',
                              'outputs': [{'title': 'example prior title'}]}}
    task_journal.write_state('privacy-task', context)
    assert task_journal.read_state('privacy-task')['workflow_definition'] == context['workflow_definition']
    sealed, _, _ = task_journal.seal_for_principal(context, 'observer')
    assert sealed['run_id'] == 'example-run'
    assert sealed['workflow_definition']['inputs'] == ['[withheld by test gate]']
    assert sealed['workflow_definition']['output']['path'] == '[withheld by test gate]'
    assert sealed['workflow_baseline']['outputs'][0]['title'] == '[withheld by test gate]'
    assert 'example quality requirement' in seen


def test_legacy_workflow_tools_use_shared_operations(monkeypatch):
    from agent_friday.services import workflow_operations
    calls = []
    def execute(action, args, context):
        calls.append((action, args, context))
        return {'workflow': {'name': 'Brief', 'slug': 'brief', 'steps': [{}, {}]}, 'task_id': 'example-task'}
    monkeypatch.setattr(workflow_operations, 'execute', execute)
    token = ag._CURRENT_CONVERSATION.set('conv-a')
    try:
        assert 'saved with 2 steps' in ag._tool_create_workflow({'name': 'Brief', 'steps': []})
        assert 'example-task' in ag._tool_run_workflow({'name': 'Brief'})
    finally:
        ag._CURRENT_CONVERSATION.reset(token)
    assert [call[0] for call in calls] == ['create', 'run']
    assert all(call[2]['conversation_id'] == 'conv-a' for call in calls)


def test_edited_workflow_does_not_reuse_old_source_baseline(fake_spawn):
    saved = ag.save_workflow_chain(definition(output={'kind': 'reply'},
        steps=[{'name': 'Check', 'prompt': 'Compare selected sources'}]))
    first = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[first].update(status='complete', result='Source A has four incidents.',
                          verification={'verified': True}, result_fingerprint='source-a')
    ag.save_workflow_chain(dict(saved, inputs=['A different source B']))
    second = ag.run_workflow_chain(saved['slug'])
    assert ag.TASKS[second]['workflow_baseline'] is None
    assert 'Source A has four incidents.' not in ag.TASKS[second]['prompt']


@pytest.mark.parametrize('prior_status', ['failed', 'completed_unverified'])
def test_change_only_delivers_first_verified_recovery(monkeypatch, fake_spawn, prior_status):
    saved = ag.save_workflow_chain(definition(output={'kind': 'reply'},
        steps=[{'name': 'Check', 'prompt': 'Compare selected sources', 'retries': 0}]))
    first = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[first].update(status=prior_status, result='A partial reading of current source facts.',
                          ended=10, verification={'verified': False}, result_fingerprint='same-facts')
    second = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[second].update(status='complete', result='A complete reading of current source facts.',
                           verification={'verified': True}, result_fingerprint='same-facts')
    messages = []
    monkeypatch.setattr(conversations, 'messages', lambda cid: messages)
    monkeypatch.setattr(conversations, 'append', lambda cid, msg, **kw: messages.append(msg) or msg)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: kw)
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: None)
    ag._report_task_completion(second, 'Check', 'complete', ag.TASKS[second]['result'])
    assert len(messages) == 1 and ag.TASKS[second]['delivery']['status'] == 'delivered'


def test_delivery_retry_uses_existing_result_and_never_spawns_or_replays_tools(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition(output={'kind': 'reply'}, notify='on_complete',
        steps=[{'name': 'Brief', 'prompt': 'Write the brief'}]))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='The checked brief is available for review.',
                         delivery={'status': 'failed'}, verification={'verified': True})
    def forbidden(*args, **kwargs):
        pytest.fail('A delivery retry must not execute workflow work')
    monkeypatch.setattr(ag, '_spawn_task', forbidden)
    monkeypatch.setattr(ag, '_execute_tool', forbidden)
    messages = []
    monkeypatch.setattr(conversations, 'messages', lambda cid: messages)
    monkeypatch.setattr(conversations, 'append', lambda cid, msg, **kw: messages.append(msg) or msg)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: kw)
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: None)
    result = ag.retry_workflow_delivery(saved['slug'], ag.TASKS[task]['run_id'])
    assert result['delivery']['status'] == 'delivered' and len(messages) == 1
    ag.TASKS[task]['delivery'] = {'status': 'failed'}
    ag.retry_workflow_delivery(saved['slug'], ag.TASKS[task]['run_id'])
    assert len(messages) == 1 and len(ag.TASKS) == 1


def test_trusted_nested_context_is_bound_only_during_handler_execution(monkeypatch):
    import json
    from agent_friday.services import tool_hooks, model_router
    monkeypatch.setattr(tool_hooks, 'run_pre_hooks', lambda ctx: tool_hooks.ALLOW)
    monkeypatch.setattr(tool_hooks, 'run_post_hooks', lambda ctx, result: result)
    monkeypatch.setattr(model_router, 'announce_tool', lambda *a: None)
    result = ag._execute_tool('read_skill', {'name': 'example-skill'},
        session_ctx={'authenticated': True, 'is_background_task': True,
                     'task_id': 'example-parent', 'schedule_id': 'example-schedule',
                     'conversation_id': 'conv-a'},
        handler=lambda args: ag._workflow_caller_context())
    context = json.loads(result)
    assert context['nested_execution'] and context['task_id'] == 'example-parent'
    assert context['schedule_id'] == 'example-schedule' and context['conversation_id'] == 'conv-a'
    assert ag._CURRENT_TOOL_CONTEXT.get() is None
    assert not ag._workflow_caller_context()['nested_execution']


def test_legacy_run_from_background_cannot_drop_parent_authority(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition())
    token = ag._CURRENT_TOOL_CONTEXT.set({'is_background_task': True,
        'task_id': 'example-parent', 'conversation_id': 'conv-a'})
    try:
        result = ag._tool_run_workflow({'name': saved['slug']})
    finally:
        ag._CURRENT_TOOL_CONTEXT.reset(token)
    assert result.startswith('run_workflow error:')
    assert not fake_spawn


def test_durable_stop_survives_state_updates_and_prevents_queue_readmission(monkeypatch, fake_spawn):
    from agent_friday.services import reconcile
    saved = ag.save_workflow_chain(definition())
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task]['status'] = 'queued-for-seat'
    task_journal.write_state(task, ag.TASKS[task])
    task_journal.request_stop(task)
    task_journal._STOP_REQUESTED.clear()  # new process has no in-memory request
    ag._task_set(task, status='queued-for-seat')
    assert task_journal.read_state(task)['stop_requested']
    monkeypatch.setattr(reconcile, '_tasks', lambda: (ag.TASKS, ag.TASKS_LOCK))
    result = reconcile.readmit_queued()
    assert result['readmitted'] == []
    assert len(ag.TASKS) == 1 and ag.TASKS[task]['status'] == 'cancelled'
    assert task_journal.read_state(task)['status'] == 'cancelled'


def test_worker_and_transcript_resume_revalidate_moved_owner_before_provider(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition())
    task = ag.run_workflow_chain(saved['slug'])
    monkeypatch.setattr(conversations, 'load', lambda cid: {'id': cid, 'project': 'another-project'})
    def forbidden(*args, **kwargs):
        pytest.fail('No provider may run after its owner becomes invalid')
    monkeypatch.setattr(ag, '_task_worker_untraced', forbidden)
    monkeypatch.setattr(ag, '_call_claude_agent', forbidden)
    assert ag._task_worker(task, 'Brief', 'Prepare source brief') is None
    assert ag.TASKS[task]['status'] == 'failed'
    with pytest.raises(task_resume.ResumeRefused, match='destination chat'):
        task_resume.resume(task)


def test_transcript_resume_forces_recorded_task_and_grant_identity(monkeypatch):
    task = 'example-resumed-task'
    ag.TASKS[task] = {'task_id': task, 'status': 'interrupted', 'conversation_id': 'conv-a',
                      'schedule_id': 'example-schedule'}
    monkeypatch.setattr(task_resume, 'resumability', lambda tid:
        {'resumable': True, 'needs_confirmation': False, 'iteration': 1})
    monkeypatch.setattr(task_resume, 'read', lambda tid: {
        'convo': [{'role': 'user', 'content': 'Resume this example work.'}],
        'run_context': {'conversation_id': 'conv-a', 'schedule_id': 'example-schedule'}})
    monkeypatch.setattr(task_resume, '_bump_attempts', lambda tid: None)
    seen = []
    monkeypatch.setattr(ag, '_call_claude_agent', lambda convo, **kw:
        seen.append(kw['session_ctx']) or ('A substantive response about the example work.', []))
    task_resume.resume(task, session_ctx={'task_id': 'wrong-task', 'schedule_id': 'wrong-schedule'})
    assert seen[0]['task_id'] == task and seen[0]['schedule_id'] == 'example-schedule'
    assert seen[0]['conversation_id'] == 'conv-a'


def test_delivery_revalidates_project_membership_without_leaking_result(monkeypatch, fake_spawn):
    saved = ag.save_workflow_chain(definition(notify='on_complete',
        steps=[{'name': 'Brief', 'prompt': 'Write the brief'}]))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='Private results for the original project.')
    monkeypatch.setattr(conversations, 'load', lambda cid: {'id': cid, 'project': 'moved-project'})
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid ownership must not send the result body anywhere')
    monkeypatch.setattr(conversations, 'append', forbidden)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', forbidden)
    ag._report_task_completion(task, 'Brief', 'complete', ag.TASKS[task]['result'])
    assert ag.TASKS[task]['delivery']['status'] == 'failed'
    assert ag.TASKS[task]['delivery']['notification'] == 'not_sent'


def test_failed_intermediate_step_exposes_its_delivery_and_checks(fake_spawn):
    saved = ag.save_workflow_chain(definition())
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='failed', verification={'status': 'unverified'},
                         delivery={'status': 'failed', 'reason': 'Store unavailable'})
    state = ag.chain_run_status(saved['slug'])
    assert state['state'] == 'failed' and state['steps'][1]['status'] == 'pending'
    assert state['verification']['status'] == 'unverified'
    assert state['delivery']['status'] == 'failed'



@pytest.mark.parametrize('settings_result', [{'off_record': False}, None],
                         ids=['saved-settings', 'missing-settings'])
def test_delivery_serializes_owner_move_with_canonical_append(monkeypatch, fake_spawn, settings_result):
    import threading
    saved = ag.save_workflow_chain(definition(notify='on_complete',
        steps=[{'name': 'Brief', 'prompt': 'Write the brief'}]))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='Private results for the original project.')
    owner = conversations.load('conv-research')
    reading, attempted = threading.Event(), threading.Event()
    acquired, writes, settings_seen, failures = [], [], [], []
    settings = settings_result or {}
    monkeypatch.setattr(ag, '_load_settings', lambda: settings_result)

    def move():
        if not reading.wait(2):
            failures.append('Delivery never read the canonical conversation.')
            attempted.set()
            return
        held = conversations._LOCK.acquire(blocking=False)
        acquired.append(held)
        if held:
            owner['project'] = 'moved-project'
            conversations._LOCK.release()
            attempted.set()
        else:
            attempted.set()
            with conversations._LOCK:
                owner['project'] = 'moved-project'

    def messages(cid):
        if not reading.is_set():
            reading.set()
            if not attempted.wait(2):
                failures.append('The ownership mutation never attempted the lock.')
        return [row[1] for row in writes]

    def append(cid, msg, **kwargs):
        writes.append((owner.get('project'), msg))
        settings_seen.append(kwargs.get('settings'))
        return msg

    monkeypatch.setattr(conversations, 'messages', messages)
    monkeypatch.setattr(conversations, 'append', append)
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: kw)
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: None)
    worker = threading.Thread(target=move, daemon=True)
    worker.start()
    try:
        ag._report_workflow_completion(task, 'Brief', 'complete', ag.TASKS[task]['result'])
    finally:
        reading.set()
        worker.join(2)
    assert not worker.is_alive() and not failures
    assert acquired == [False], 'Refiling must wait for canonical result delivery.'
    assert len(writes) == 1 and writes[0][0] == 'project-research'
    assert settings_seen == [settings]
    assert owner['project'] == 'moved-project'
    assert ag.TASKS[task]['delivery']['status'] == 'delivered'


@pytest.mark.parametrize('mutation, initial_project', [
    ('move', 'project-research'), ('archive', 'project-research'),
    ('delete', 'project-research'), ('move', None)],
    ids=['move', 'archive', 'delete', 'file-unfiled-chat'])
def test_delivery_rechecks_owner_after_settings_resolution(monkeypatch, fake_spawn, mutation, initial_project):
    import threading
    conversations.load('conv-research')['project'] = initial_project
    saved = ag.save_workflow_chain(definition(notify='on_complete', project_id=initial_project,
        steps=[{'name': 'Brief', 'prompt': 'Write the brief'}]))
    task = ag.run_workflow_chain(saved['slug'])
    ag.TASKS[task].update(status='complete', result='Private results for the original project.')
    owner = dict(conversations.load('conv-research'))
    started, changed = threading.Event(), threading.Event()
    effects, failures = [], []
    monkeypatch.setattr(conversations, 'load', lambda cid: owner or None)

    def change_owner():
        if not started.wait(2):
            failures.append('Settings were not resolved before canonical delivery.')
            changed.set()
            return
        with conversations._LOCK:
            if mutation == 'delete':
                owner.clear()
            elif mutation == 'archive':
                owner['status'] = 'archived'
            else:
                owner['project'] = 'moved-project'
        changed.set()

    def settings():
        started.set()
        if not changed.wait(2):
            failures.append('Settings resolution held the conversation lock.')
        return {'off_record': False}

    monkeypatch.setattr(ag, '_load_settings', settings)
    monkeypatch.setattr(conversations, 'messages', lambda cid: [])
    monkeypatch.setattr(conversations, 'append', lambda *a, **kw: effects.append('transcript'))
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: effects.append('notification'))
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: effects.append('voice'))
    worker = threading.Thread(target=change_owner, daemon=True)
    worker.start()
    try:
        ag._report_workflow_completion(task, 'Brief', 'complete', ag.TASKS[task]['result'])
    finally:
        started.set()
        worker.join(2)
    assert not worker.is_alive() and not failures
    assert effects == []
    assert ag.TASKS[task]['delivery']['status'] == 'failed'
    assert ag.TASKS[task]['delivery']['notification'] == 'not_sent'



def test_unfiled_run_owner_is_fixed_through_delivery_retry_and_recovery(monkeypatch, fake_spawn):
    owner = conversations.load('conv-research')
    owner['project'] = None
    saved = ag.save_workflow_chain(definition(notify='on_complete', project_id=None,
        steps=[{'name': 'Brief', 'prompt': 'Write the brief'}]))
    task = ag.run_workflow_chain(saved['slug'])
    assert ag.TASKS[task]['project_id'] is None
    ag.TASKS[task].update(status='complete', result='Private results for the unfiled chat.')
    owner['project'] = 'project-research'
    effects = []
    monkeypatch.setattr(conversations, 'messages', lambda cid: [])
    monkeypatch.setattr(conversations, 'append', lambda *a, **kw: effects.append('transcript'))
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, 'push', lambda **kw: effects.append('notification'))
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, 'deliver', lambda *a, **kw: effects.append('voice'))
    ag._report_workflow_completion(task, 'Brief', 'complete', ag.TASKS[task]['result'])
    assert effects == [], 'Filing a chat must not reassign the authority of an existing run.'
    assert ag.TASKS[task]['delivery']['status'] == 'failed'
    with pytest.raises(ValueError, match='destination chat'):
        ag.retry_workflow_delivery(saved['slug'], ag.TASKS[task]['run_id'])
    assert not ag._prepare_task_start(task)
    assert ag.TASKS[task]['status'] == 'failed'
    with pytest.raises(task_resume.ResumeRefused, match='destination chat'):
        task_resume.resume(task)
