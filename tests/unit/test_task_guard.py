"""Synthetic HTTP/storage boundary checks; no service calls or real tenant files."""
import json
import re
from types import SimpleNamespace

import pytest

import config_db
import health_db
import mcp_tools
import notify
import task_agent
import task_guard
import tasks_db

pytestmark = pytest.mark.unit


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    health_db._migrate_tasks()
    secret_dir = tmp_path / 'secrets'
    secret_dir.mkdir()
    key = 'synthetic-bearer-not-a-real-credential-123456'
    (secret_dir / 'typesafe_key.txt').write_text(key)
    monkeypatch.setenv('HEALTH_SECRETS_DIR', str(secret_dir))
    monkeypatch.setattr(mcp_tools, '_redactor', lambda: re.compile('PRIVATE_TOKEN'))
    monkeypatch.setattr(task_agent, 'judge_task_duplicates', lambda *a: {})
    monkeypatch.setattr(task_agent, 'addressed_to_patient', lambda xs: {
        x['id']: {'verdict': 'patient', 'reason': 'addressed'} for x in xs})
    monkeypatch.setattr(task_agent, 'should_ask_again', lambda *a: (True, 'test'))
    calls = []
    answer = {'choice': 'duplicate', 'confidence': 0.8,
              'probabilities': {'duplicate': 0.8, 'contradicts': 0.1, 'new': 0.1}}
    w = SimpleNamespace(db=db, calls=calls, answer=answer, key=key, secrets=secret_dir,
                        status=200, error=None, clock=0.0, elapsed=0.0)
    monkeypatch.setattr(task_guard, '_monotonic', lambda: w.clock)

    class HTTP:
        def __init__(self, host, timeout):
            self.timeout = timeout
            assert host == 'api.typesafe.ai'

        def request(self, method, url, body, headers):
            calls.append((json.loads(body), headers, self.timeout))
            assert (method, url) == ('POST', '/v1/systemone')
            w.clock += min(w.elapsed, self.timeout)
            if w.error:
                raise w.error

        def getresponse(self):
            return SimpleNamespace(status=w.status, read=lambda n: json.dumps(
                {'answers': {'pair': w.answer}}).encode())

        def close(self):
            pass

    monkeypatch.setattr(task_guard.http.client, 'HTTPSConnection', HTTP)
    w.mp = monkeypatch
    return w


def extract(w, *items):
    response = SimpleNamespace(content=[SimpleNamespace(text=json.dumps(items))], stop_reason='end_turn')
    w.mp.setattr(task_agent, '_get_client', lambda: SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: response)))
    return task_agent.extract_tasks_from_report('synthetic report', 'synthetic_source')


def journal(w):
    return [dict(r) for r in w.db.fetchall('SELECT * FROM task_guard_journal ORDER BY new_task_id, open_task_id')]


@pytest.mark.parametrize('deadline,expected', [(None, 'duplicate'), ('2030-02-01', 'contradicts')])
def test_deterministic_pair_never_calls_http(world, deadline, expected):
    w = world
    old = w.db.add_task('ALT', type='lab_test')
    saved = extract(w, {'type': 'lab_test', 'content': 'ALT', 'deadline': deadline})
    row, = journal(w)
    assert saved and row['new_task_id'] == saved[0]['id'] and row['open_task_id'] == old
    assert (row['verdict'], row['judge'], row['confidence']) == (expected, 'det', 1.0)
    assert w.calls == []
    assert w.db.count('tasks', "status='open'") == 2


def test_jev_redacts_every_field_and_preserves_addressing(world, caplog):
    w = world
    old = w.db.add_task('PRIVATE_TOKEN old', type='action', source='PRIVATE_TOKEN')
    config_db.upsert_config('tasks.guard_timeout_seconds', value_num=0.4)
    saved = extract(w, {'type': 'question', 'content': 'PRIVATE_TOKEN new',
                        'origin': 'PRIVATE_TOKEN', 'reason': 'visible reason'})
    row, = journal(w)
    assert row['new_task_id'] == saved[0]['id'] and row['open_task_id'] == old
    assert (row['verdict'], row['judge'], row['confidence']) == ('duplicate', 'jev', 0.8)
    body, headers, timeout = w.calls[0]
    assert 'PRIVATE_TOKEN' not in body['state'] and '[скрыто]' in body['state']
    assert headers['Authorization'] == 'Bearer ' + w.key and timeout == 0.4
    assert body['model'] == 'jev-latest' and set(body['questions']['pair']['criteria']) == task_guard._CHOICES
    task = tasks_db.get_task(saved[0]['id'])
    assert task['judge_verdict'] == 'patient' and task['judge_reason'] == 'addressed'
    assert task['reason'] == 'visible reason'
    assert 'jev' not in task_agent.format_tasks_message(saved)
    assert w.key not in caplog.text + json.dumps(journal(w)) + json.dumps(body)


@pytest.mark.parametrize('failure', ['timeout', 'empty_key', 'http', 'bad_choice', 'nan', 'bad_probs'])
def test_failures_keep_task_and_record_unjudged(world, failure, caplog):
    w = world
    w.db.add_task('old', type='action')
    if failure == 'timeout':
        w.error = TimeoutError(w.key)
    elif failure == 'empty_key':
        (w.secrets / 'typesafe_key.txt').write_text('')
    elif failure == 'http':
        w.status = 503
    elif failure == 'bad_choice':
        w.answer['choice'] = w.key
    elif failure == 'nan':
        w.answer['confidence'] = float('nan')
    else:
        w.answer['probabilities'] = []
    saved = extract(w, {'type': 'action', 'content': 'new'})
    assert len(saved) == 1 and tasks_db.get_task(saved[0]['id'])['status'] == 'open'
    row, = journal(w)
    assert row['verdict'] == 'unjudged' and row['confidence'] is None
    assert w.key not in caplog.text + json.dumps(journal(w)) + notify.faults_journal().read_text()


def test_off_reads_no_tasks_or_key_and_calls_no_judge(world):
    config_db.upsert_config('tasks.guard_mode', value_text='off')
    world.mp.setattr(tasks_db, 'get_open_tasks', lambda **kw: pytest.fail('snapshot in off'))
    assert len(extract(world, {'type': 'action', 'content': 'new'})) == 1
    assert journal(world) == [] and world.calls == []


def test_enforce_is_shadow_and_all_open_tasks_and_siblings_are_seen(world, caplog):
    w = world
    config_db.upsert_config('tasks.guard_mode', value_text='enforce')
    for i in range(23):
        w.db.add_task(f'old {i}', type='action')
    w.db.add_task('closed', status='completed')
    saved = extract(w, {'type': 'followup', 'content': 'new one'}, {'type': 'action', 'content': 'new two'})
    assert len(saved) == 2 and len(journal(w)) == 47 and len(w.calls) == 47
    assert 'using shadow' in caplog.text


def test_pair_interface_secret_in_state_never_leaves_machine(world):
    got = task_guard.judge_pair({'content': world.key}, {'content': 'old'}, timeout=1)
    assert got['verdict'] == 'unjudged' and got['reason'] == 'secret_blocked'
    assert world.calls == []


def test_daily_fault_throttle_survives_calls_and_is_tenant_scoped(world):
    from datetime import datetime, timedelta, timezone
    import _time_inject
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    world.mp.setattr(_time_inject, 'get_now', lambda: now)
    faults = []
    world.mp.setattr(notify, 'fault', lambda tech, **kw: faults.append((tech, kw)))
    task_guard.report_failure()
    task_guard.report_failure()
    now += timedelta(hours=23)
    task_guard.report_failure()
    assert len(faults) == 1 and faults[0][1]['person_key'] is None
    now += timedelta(hours=1)
    task_guard.report_failure()
    assert len(faults) == 2
    world.mp.setattr(health_db, 'DB_PATH', world.secrets / 'another_tenant' / 'health.db')
    task_guard.report_failure()
    assert len(faults) == 3


def test_migration_repeat_upgrade_and_audit_failure_do_not_lose_task(world, caplog):
    w = world
    w.db.execute('DROP TABLE task_guard_journal')
    health_db._migrate_tasks()
    health_db._migrate_tasks()
    w.db.add_task('old', type='action')
    w.db.execute("CREATE TRIGGER refuse_audit BEFORE INSERT ON task_guard_journal BEGIN SELECT RAISE(ABORT, 'no audit'); END")
    saved = extract(w, {'type': 'action', 'content': 'new'})
    assert len(saved) == 1 and journal(w) == []
    assert 'audit storage failed' in caplog.text


def test_memory_creation_uses_same_shadow_guard(world):
    w = world
    for key, value in (("questions.memory_window_days", 45), ("questions.promote_batch", 5),
                       ("questions.max_per_day", 20)):
        config_db.upsert_config(key, value_num=value)
    w.db.add_task('unrelated action', type='action')
    w.db.execute("INSERT INTO memory_facts (mem_class, value, source) VALUES ('question', 'synthetic question?', 'conversation')")
    fid = w.db.fetchone('SELECT max(id) FROM memory_facts')[0]
    w.mp.setattr(task_agent, 'addressed_to_patient', lambda xs: {
        str(fid): {'verdict': 'patient', 'reason': 'addressed', 'ru': 'synthetic question?'}})
    saved = task_agent.promote_memory_questions()
    assert len(saved) == 1
    row, = journal(w)
    assert row['new_task_id'] == saved[0]['id'] and row['judge'] == 'jev'
    assert tasks_db.get_task(saved[0]['id'])['judge_verdict'] == 'patient'


def test_pair_judge_can_be_replaced_and_empty_snapshot_is_not_a_new_verdict(world):
    assert task_guard.audit_new_task({'type': 'action', 'content': 'new'}) == []
    tid = world.db.add_task('old', type='action')
    got = task_guard.audit_new_task({'type': 'action', 'content': 'new'}, judge=lambda *a, **kw: {
        'verdict': 'new', 'confidence': 0.6, 'judge': 'jev', 'reason': 'test'})
    assert got[0]['open_task_id'] == tid and got[0]['verdict'] == 'new'
    assert world.calls == []


def test_fingerprint_skip_has_no_orphan_audit(world):
    world.db.add_task('old', type='action', fingerprint='same')
    assert extract(world, {'type': 'action', 'content': 'new', 'fingerprint': 'same'}) == []
    assert journal(world) == []


def test_config_seed_upgrade_keeps_operator_choice(world):
    config_db.upsert_config('tasks.guard_mode', value_text='off')
    config_db.upsert_config('tasks.guard_timeout_seconds', value_num=0.3)
    config_db.upsert_config('tasks.guard_budget_seconds', value_num=7)
    health_db._migrate_patient_profile_and_config()
    assert config_db.get_config('tasks.guard_budget_seconds') == 7
    assert config_db.get_config('tasks.guard_mode') == 'off'
    assert config_db.get_config('tasks.guard_timeout_seconds') == 0.3


def test_shared_throttle_claim_is_serialized(world):
    from concurrent.futures import ThreadPoolExecutor
    state = world.secrets / 'repeat.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: notify.claim_repeat('same', state, 1000, 86400), range(8)))
    assert sum(claims) == 1


@pytest.mark.parametrize('failure', ['timeout', 'http'])
def test_breaker_spans_report_batch_but_det_continues_and_next_run_resets(world, failure):
    w = world
    w.db.add_task('unrelated', type='action', priority='critical')
    w.db.add_task('ALT', type='lab_test', priority='low')
    w.db.add_task('another', type='action')
    if failure == 'timeout':
        w.error = TimeoutError('synthetic transport failure')
    else:
        w.status = 503
    saved = extract(w, {'type': 'action', 'content': 'first'},
                    {'type': 'lab_test', 'content': 'ALT'})
    assert len(saved) == 2 and len(w.calls) == 1
    counts = {r['reason']: r['n'] for r in w.db.fetchall(
        'SELECT reason, count(*) AS n FROM task_guard_journal GROUP BY reason')}
    cause = 'provider_unavailable' if failure == 'timeout' else 'http_error'
    assert counts == {cause: 1, 'breaker_open': 5, 'shared_analyte_deadline': 1}
    assert len(journal(w)) == 7
    assert all(r['verdict'] == 'unjudged' for r in journal(w) if r['judge'] == 'jev')
    assert extract(w, {'type': 'action', 'content': 'next report'})
    assert len(w.calls) == 2  # breaker belongs to a run, never the process


def test_budget_spans_batch_caps_timeout_and_preserves_det(world):
    w = world
    config_db.upsert_config('tasks.guard_budget_seconds', value_num=2.5)
    w.elapsed = 2.0
    w.db.add_task('old action', type='action', priority='critical')
    w.db.add_task('another action', type='action', priority='high')
    w.db.add_task('ALT', type='lab_test', priority='low')
    faults = []
    w.mp.setattr(notify, 'fault', lambda *a, **kw: faults.append(a))
    saved = extract(w, {'type': 'action', 'content': 'first'},
                    {'type': 'lab_test', 'content': 'ALT'})
    assert len(saved) == 2 and len(w.calls) == 2
    assert [c[2] for c in w.calls] == [2.0, 0.5]
    counts = {r['reason']: r['n'] for r in w.db.fetchall(
        'SELECT reason, count(*) AS n FROM task_guard_journal GROUP BY reason')}
    assert counts == {'jev-latest': 2, 'budget': 4, 'shared_analyte_deadline': 1}
    assert faults == []


def test_absent_key_is_not_configured_without_fault_and_logged_once(world, caplog):
    w = world
    caplog.set_level('INFO', logger='task_guard')
    (w.secrets / 'typesafe_key.txt').unlink()
    w.db.add_task('old', type='action', priority='high')
    w.db.add_task('ALT', type='lab_test', priority='low')
    faults = []
    w.mp.setattr(notify, 'fault', lambda *a, **kw: faults.append(a))
    saved = extract(w, {'type': 'action', 'content': 'first'},
                    {'type': 'lab_test', 'content': 'ALT'})
    assert len(saved) == 2 and w.calls == [] and faults == []
    assert {r['reason'] for r in journal(w)} == {'not_configured', 'shared_analyte_deadline'}
    assert extract(w, {'type': 'action', 'content': 'another run'})
    assert caplog.text.count('model not configured for tenant') == 1
    assert not notify.faults_journal().exists() or 'task_guard:' not in notify.faults_journal().read_text()
    # Another tenant must not inherit configuration or log suppression.
    other = w.secrets / 'other'
    other.mkdir()
    w.mp.setenv('HEALTH_SECRETS_DIR', str(other))
    assert task_guard.judge_pair({'content': 'x'}, {'content': 'y'}, timeout=1)['reason'] == 'not_configured'
    assert caplog.text.count('model not configured for tenant') == 2
    assert faults == [] and w.calls == []


def test_empty_key_is_configuration_fault(world):
    w = world
    (w.secrets / 'typesafe_key.txt').write_text('  ')
    w.db.add_task('old', type='action')
    faults = []
    w.mp.setattr(notify, 'fault', lambda *a, **kw: faults.append((a, kw)))
    assert extract(w, {'type': 'action', 'content': 'first'})
    row, = journal(w)
    assert row['verdict'] == 'unjudged' and row['reason'] == 'empty_key'
    assert w.calls == [] and len(faults) == 1 and faults[0][1]['person_key'] is None


def test_memory_creation_shares_one_breaker(world):
    w = world
    for key, value in (("questions.memory_window_days", 45), ("questions.promote_batch", 5),
                       ("questions.max_per_day", 20)):
        config_db.upsert_config(key, value_num=value)
    w.db.add_task('old action', type='action')
    w.db.execute("INSERT INTO memory_facts (mem_class, value, source) VALUES ('question', 'first?', 'conversation')")
    w.db.execute("INSERT INTO memory_facts (mem_class, value, source) VALUES ('question', 'second?', 'conversation')")
    rows = w.db.fetchall('SELECT id, value FROM memory_facts')
    w.mp.setattr(task_agent, 'addressed_to_patient', lambda xs: {
        str(r['id']): {'verdict': 'patient', 'reason': 'addressed', 'ru': r['value']} for r in rows})
    w.error = TimeoutError('synthetic timeout')
    saved = task_agent.promote_memory_questions()
    assert len(saved) == 2 and len(w.calls) == 1
    assert [r['reason'] for r in journal(w)].count('breaker_open') == 2


def test_budget_seed_and_fallback_share_one_default(world):
    health_db._migrate_patient_profile_and_config()
    assert config_db.get_config('tasks.guard_budget_seconds') == config_db.TASK_GUARD_BUDGET_SECONDS
    for value in (None, 0, -1, 'invalid'):
        if value is None:
            world.db.execute("DELETE FROM system_config WHERE key='tasks.guard_budget_seconds'")
        else:
            config_db.upsert_config('tasks.guard_budget_seconds', value_text=str(value))
        batch = task_guard.start_batch()
        task_guard.audit_new_task({'type': 'action', 'content': 'new'}, batch=batch)
        assert batch.deadline == world.clock + config_db.TASK_GUARD_BUDGET_SECONDS



def test_budget_starts_at_batch_creation_not_first_pair(world):
    config_db.upsert_config('tasks.guard_budget_seconds', value_num=3)
    batch = task_guard.start_batch()
    world.clock += 3
    world.db.add_task('old', type='action')
    result = task_guard.audit_new_task({'type': 'action', 'content': 'new'}, batch=batch)
    assert result[0]['reason'] == 'budget' and world.calls == []


def test_key_can_be_configured_between_batches(world):
    path = world.secrets / 'typesafe_key.txt'
    path.unlink()
    world.db.add_task('old', type='action')
    assert extract(world, {'type': 'action', 'content': 'unconfigured run'})
    assert world.calls == []
    path.write_text(world.key)
    assert extract(world, {'type': 'action', 'content': 'configured run'})
    assert len(world.calls) == 2
