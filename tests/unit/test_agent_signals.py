"""Synthetic end-to-end signal -> SQLite ledger -> guarded spool checks.

No real transport. Scheduled functions and sensor compiled with the existing
agent_mail harness, excluding unrelated top-level bot/integrity execution.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import importlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import agent_mail
import agent_signals as signals
import config_db
import health_db
import labs_db
import mcp_tools
import pii_census
import safety_net
from _time_inject import set_test_clock
from tests.unit.test_agent_mail import _load_functions

DAY = date(2026, 4, 20)
NOW = datetime(2026, 4, 20, 9, tzinfo=timezone.utc)


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    set_test_clock(NOW)
    root = tmp_path / 'mail'
    for folder in ('out', 'sent', 'in', 'ack', 'acked'):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv('AGENT_MAIL_DIR', str(root))
    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    monkeypatch.setenv('HEALTH_SECRETS_DIR', str(secrets))
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private' / 'pii_terms.yaml').write_text('literals: {}\npatterns: {}\n')
    pattern = pii_census.pattern
    monkeypatch.setattr(pii_census, 'pattern', lambda classes: pattern(classes, root=tmp_path))
    monkeypatch.setattr(mcp_tools, '_redactor_cache', [])
    health_db._migrate_agent_mail()
    health_db._seed_safety_lifestyle_thresholds()
    config_db.upsert_config('agent_mail.assistant', value_text='assistant-example')
    for metric, floor in [('sleep_total', 6), ('readiness', 65)]:
        db.execute("UPDATE absolute_thresholds SET value=?,source='p10_personal' "
                   "WHERE metric=? AND variant='' AND band_label='' AND direction='floor'", (floor, metric))
    # Одним соединением, закрытым явно: 41 открытие подряд в полном прогоне дважды давало
    # «unable to open database file» при подготовке (06.11, гипотеза — предел дескрипторов).
    from contextlib import closing
    with closing(db.conn()) as c, c:
        c.executemany("INSERT OR REPLACE INTO daily_metrics "
                      "(date,sleep_total,readiness,hrv,resting_hr,stress_high_min) VALUES (?,?,?,?,?,?)",
                      [(str(DAY - timedelta(days=ago)), 8, 80, 50, 60, 10) for ago in range(41)])
    # A deterministic state evaluation must never initialize/mutate its sources.
    def forbidden_init():
        raise AssertionError('state_signals called init_db')
    monkeypatch.setattr(health_db, 'init_db', forbidden_init)
    return SimpleNamespace(db=db, root=root)


def _update(world, **values):
    sql = ','.join(f'{key}=?' for key in values)
    world.db.execute(f'UPDATE daily_metrics SET {sql} WHERE date=?', (*values.values(), str(DAY)))


def _letters(world, kind=None):
    letters = [json.loads(path.read_text()) for path in sorted((world.root / 'out').glob('*.json'))]
    return [letter for letter in letters if kind is None or letter['kind'] == kind]


def _bootstrap(world):
    # First run sends the mandatory regimen card, even when there are no supplements.
    signals.run(DAY)
    assert _letters(world, 'state') == []
    for path in (world.root / 'out').glob('*.json'):
        path.unlink()


@pytest.mark.parametrize('metric,bad,key', [
    ('sleep_total', 5, 'sleep_total'), ('readiness', 60, 'readiness'),
    ('resting_hr', 70, 'illness_rhr'), ('hrv', 30, 'illness_hrv'),
    ('stress_high_min', 11, 'stress'),
])
def test_each_signal_abnormal_and_normal(world, metric, bad, key):
    assert signals.state_signals(DAY) == []
    _update(world, **{metric: bad})
    found = signals.state_signals(DAY)
    assert key in {s['key'] for s in found}
    signal = next(s for s in found if s['key'] == key)
    assert set(signal) == {'key', 'what', 'why', 'proposal', 'urgency', 'facts'}
    assert signal['facts']['day'] == str(DAY)


def test_sleep_series_contiguous_and_configurable(world):
    for ago in range(3):
        world.db.execute('UPDATE daily_metrics SET sleep_total=7 WHERE date=?', (str(DAY-timedelta(days=ago)),))
    assert [s['key'] for s in signals.state_signals(DAY)] == ['sleep_series']
    world.db.execute('DELETE FROM daily_metrics WHERE date=?', (str(DAY-timedelta(days=1)),))
    assert signals.state_signals(DAY) == []
    cfg = signals.policy()
    cfg['sleep_nights'] = 1
    config_db.upsert_config('agent_signals.policy', value_json=cfg)
    assert signals.state_signals(DAY)[0]['key'] == 'sleep_series'


def test_threshold_is_data(world):
    _update(world, sleep_total=6.5)
    assert signals.state_signals(DAY) == []
    world.db.execute("UPDATE absolute_thresholds SET value=7 WHERE metric='sleep_total' AND variant='' AND band_label=''")
    assert [s['key'] for s in signals.state_signals(DAY)] == ['sleep_total']
    world.db.execute("UPDATE absolute_thresholds SET value=6 WHERE metric='sleep_total' AND variant='' AND band_label=''")
    assert signals.state_signals(DAY) == []


def test_safety_result_and_rule_are_reused(world):
    _update(world, hrv=30, resting_hr=70)
    expected = {a['metric']: a for a in safety_net.check_lifestyle_alerts(DAY, initialize=False)
                if a['metric'] in ('HRV', 'ЧСС покоя')}
    found = [s for s in signals.state_signals(DAY) if s['key'].startswith('illness_')]
    assert len(found) == 2
    for signal in found:
        fact = {k: v for k, v in signal['facts'].items() if k != 'day'}
        assert fact == expected[fact['metric']]
    world.db.execute("UPDATE absolute_thresholds SET value=0.99 WHERE metric='hrv' AND variant='safety_net_rel'")
    world.db.execute("UPDATE absolute_thresholds SET value=100 WHERE metric='resting_hr' AND variant='safety_net_rel'")
    assert signals.state_signals(DAY) == []


def test_percentile_history_is_prior_and_zero_stress_counts(world):
    world.db.execute('UPDATE daily_metrics SET stress_high_min=0')
    world.db.add_daily_metrics(str(DAY+timedelta(days=1)), stress_high_min=999, sleep_total=1)
    assert health_db.get_metric_percentile('stress_high_min', 90, 90, 30, DAY) == 0
    assert health_db.get_metric_percentile('sleep_total', 50, 90, 14, DAY) == 8
    _update(world, stress_high_min=1)
    assert [s['key'] for s in signals.state_signals(DAY)] == ['stress']
    world.db.execute('DELETE FROM daily_metrics WHERE date<?', (str(DAY-timedelta(days=2)),))
    assert signals.state_signals(DAY) == []


def test_normal_sends_nothing(world, monkeypatch):
    _bootstrap(world)
    def forbidden(*args, **kwargs):
        raise AssertionError('mail on normal day')
    monkeypatch.setattr(agent_mail, 'send', forbidden)
    assert signals.run(DAY) == []
    assert signals.run(DAY, phase='afternoon') == []
    assert signals.last_run_age_hours() == 0


def test_dedup_restart_and_new_afternoon_signal(world):
    _bootstrap(world)
    _update(world, sleep_total=5, readiness=50)
    signals.run(DAY)
    importlib.reload(signals)
    signals.run(DAY)
    assert len(_letters(world, 'state')) == 1
    _update(world, stress_high_min=90)
    signals.run(DAY, phase='afternoon')
    signals.run(DAY, phase='afternoon')
    letters = _letters(world, 'state')
    assert len(letters) == 2
    afternoon = next(letter for letter in letters if 'стресс' in letter['subject'])
    assert 'Короткий сон' not in afternoon['body']
    assert 'Что случилось:' in afternoon['body'] and 'Срочность:' in afternoon['body']
    assert 'Решение — за ассистентом вместе с пользователем.' in afternoon['body']


def test_morning_budget_and_stress_already_sent(world):
    _bootstrap(world)
    _update(world, sleep_total=5, stress_high_min=90)
    signals.run(DAY)
    _update(world, readiness=50)
    signals.run(DAY)
    signals.run(DAY, phase='afternoon')
    assert len(_letters(world, 'state')) == 1


def test_concurrent_runs_do_not_duplicate(world):
    _bootstrap(world)
    _update(world, sleep_total=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: signals.run(DAY), range(2)))
    assert len(_letters(world, 'state')) == 1


def test_failed_publication_is_not_retried(world, monkeypatch):
    _bootstrap(world)
    _update(world, sleep_total=5)
    count = []
    def broken(*args, **kwargs):
        count.append(1)
        raise OSError('synthetic publication failure')
    monkeypatch.setattr(agent_mail, 'send', broken)
    with pytest.raises(OSError):
        signals.run(DAY)
    assert signals.run(DAY) == []
    assert count == [1]


def test_regimen_diff_and_return_to_previous_regimen(world):
    world.db.execute("INSERT INTO medications(name,modality,status,confirmation,dosage,frequency,notes) "
                     "VALUES ('Добавка А','supplement','active','proposed','1 мг','утром',?)",
                     ('предложено Health OS, с врачом не согласовано',))
    signals.run(DAY)
    assert len(_letters(world, 'regimen')) == 1
    assert 'с врачом не согласовано' in _letters(world, 'regimen')[0]['body']
    signals.run(DAY)
    assert len(_letters(world, 'regimen')) == 1
    world.db.execute("UPDATE medications SET frequency='вечером' WHERE name='Добавка А'")
    signals.run(DAY)
    letters = _letters(world, 'regimen')
    assert len(letters) == 2
    assert any('Поменялась схема: Добавка А' in m['body'] and 'вечером' in m['body'] for m in letters)
    world.db.execute("UPDATE medications SET frequency='утром' WHERE name='Добавка А'")
    signals.run(DAY)
    assert len(_letters(world, 'regimen')) == 3
    world.db.execute("UPDATE medications SET status='discontinued'")
    signals.run(DAY)
    assert any('Убрано: Добавка А' in m['body'] for m in _letters(world, 'regimen'))


def test_lab_due_once_per_analyte_deadline_and_grouped(world):
    for test in ('HGB', 'WBC'):
        world.db.execute("INSERT INTO lab_monitoring_schedule(test_name,interval_days,source) VALUES (?,30,'manual')", (test,))
        world.db.add_lab_result('2026-04-01', test, 10, unit='g/L', source='manual:synthetic')
    world.db.add_profile('routine.fasting_labs', value_text='true')
    signals.run(DAY)
    assert len(_letters(world, 'lab_due')) == 1
    text = _letters(world, 'lab_due')[0]['body']
    assert 'Гемоглобин' in text and '2026-05-01' in text and '2026-04-01' in text and 'натощак' in text
    signals.run(DAY)
    signals.run(DAY+timedelta(days=1))
    assert len(_letters(world, 'lab_due')) == 1
    world.db.add_lab_result('2026-04-02', 'HGB', 11, unit='g/L', source='manual:synthetic')
    signals.run(DAY+timedelta(days=2))
    assert len(_letters(world, 'lab_due')) == 2


def test_lab_boundaries_aliases_doctor_override_and_score_excluded(world):
    world.db.execute('ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_stable_days INTEGER')
    world.db.execute('ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_doctor_days INTEGER')
    world.db.execute("INSERT INTO lab_monitoring_schedule(test_name,interval_days,interval_doctor_days,source) VALUES ('HGB',1,30,'manual')")
    world.db.add_lab_result('2026-04-05', 'Гемоглобин', 10, unit='g/L', source='manual:synthetic')
    world.db.add_lab_result('2026-04-19', 'HGB', 100, unit='score', source='manual:synthetic')
    world.db.add_lab_result('2026-04-21', 'Hemoglobin', 100, unit='g/L', source='manual:synthetic')
    signals.run(DAY)  # due May 5 is 15 days away
    assert _letters(world, 'lab_due') == []
    signals.run(DAY+timedelta(days=1))  # future lab now exists, postpones due
    assert _letters(world, 'lab_due') == []
    world.db.execute("DELETE FROM lab_results WHERE date='2026-04-21'")
    signals.run(DAY+timedelta(days=1))
    assert '2026-05-05' in _letters(world, 'lab_due')[0]['body']


def test_empty_recipient_is_off(world, monkeypatch):
    config_db.upsert_config('agent_mail.assistant', value_text='')
    _update(world, sleep_total=1, stress_high_min=999)
    def forbidden(*args, **kwargs):
        raise AssertionError('disabled evaluation/send')
    monkeypatch.setattr(signals, 'state_signals', forbidden)
    monkeypatch.setattr(agent_mail, 'send', forbidden)
    assert signals.run(DAY) == []
    assert signals.last_run_age_hours() is None


def test_seed_fresh_upgrade_owner_partner_and_off_preserved(tmp_path, monkeypatch):
    private = tmp_path / 'agent_mail.yaml'
    private.write_text('owner_assistant: assistant-example\n', encoding='utf-8')
    monkeypatch.setattr(health_db, 'AGENT_MAIL_PRIVATE', private)
    for tenant, expected in [('health', 'assistant-example'), ('health_example', '')]:
        root = tmp_path / tenant
        root.mkdir()
        monkeypatch.setenv('HEALTH_DATA_DIR', str(root))
        monkeypatch.setattr(health_db, 'DB_PATH', root / 'health.db')
        health_db._migrate_patient_profile_and_config()
        health_db._migrate_agent_mail()
        assert config_db.get_config('agent_mail.assistant') == expected
        cfg = signals.policy()
        assert cfg['sleep_nights'] == 3 and cfg['max_age_hours'] == 26
        config_db.upsert_config('agent_mail.assistant', value_text='')
        cfg['lab_lead_days'] = 2
        config_db.upsert_config('agent_signals.policy', value_json=cfg)
        health_db._migrate_agent_mail()
        assert config_db.get_config('agent_mail.assistant') == ''
        assert signals.policy()['lab_lead_days'] == 2


def test_sensor_negative_control(world):
    check = _load_functions('integrity_tests.py', ['check_agent_signals_ran'])['check_agent_signals_ran']
    with pytest.raises(AssertionError):
        check()
    signals.run(DAY)
    assert check()['agent_signals_age_hours'] == 0
    set_test_clock(NOW+timedelta(hours=26))
    assert check()['agent_signals_age_hours'] == 26
    set_test_clock(NOW+timedelta(hours=26, seconds=1))
    with pytest.raises(AssertionError):
        check()
    signals.run(DAY, phase='afternoon')  # cannot mask a failed morning pass
    with pytest.raises(AssertionError):
        check()
    config_db.upsert_config('agent_mail.assistant', value_text='')
    assert check() == {'agent_signals': 'disabled'}


def test_schedule_shares_brief_time_and_configured_offsets(world, monkeypatch):
    from jobs import scheduled
    jobs = []
    removed = []
    queue = SimpleNamespace(get_jobs_by_name=lambda name: [SimpleNamespace(schedule_removal=lambda: removed.append(name))],
                            run_daily=lambda callback, **kw: jobs.append((callback, kw)))
    cfg = signals.policy()
    cfg['morning_offset_minutes'] = 23
    cfg['stress_offset_minutes'] = 19
    config_db.upsert_config('agent_signals.policy', value_json=cfg)
    config_db.upsert_config(scheduled._BRIEF_TIME_KEY, value_json={'hour':9, 'minute':10})
    tz = ZoneInfo('Asia/Tokyo')
    monkeypatch.setattr(scheduled, '_resolve_brief_tz', lambda: (tz, str(tz), True))
    monkeypatch.setattr(scheduled, '_tenant_tz', lambda default: ZoneInfo('Europe/Berlin'))
    app = SimpleNamespace(job_queue=queue)
    scheduled._apply_morning_schedule(app)
    assert [job[1]['time'] for job in jobs] == [time(9,10,tzinfo=tz), time(9,33,tzinfo=tz), time(17,19,tzinfo=ZoneInfo('Europe/Berlin'))]
    assert len(removed) == 3
    seen = []
    monkeypatch.setattr(signals, 'run', lambda day, phase: seen.append((day,phase)))
    for callback, job in jobs[1:]:
        asyncio.run(callback(SimpleNamespace(job=SimpleNamespace(data=job['data']))))
    assert seen == [(DAY,'morning'), (DAY,'afternoon')]
    jobs.clear()
    config_db.upsert_config('agent_mail.assistant', value_text='')
    scheduled._apply_morning_schedule(app)
    assert [job[1]['name'] for job in jobs] == ['morning_report']


def test_bad_optional_policy_does_not_block_brief(world, monkeypatch, caplog):
    from jobs import scheduled
    config_db.upsert_config('agent_signals.policy', value_json={'invalid': True})
    registered = []
    queue = SimpleNamespace(get_jobs_by_name=lambda name: [],
                            run_daily=lambda cb, **kw: registered.append(kw['name']))
    monkeypatch.setattr(scheduled, '_resolve_brief_tz', lambda: (timezone.utc, 'UTC', True))
    scheduled._apply_morning_schedule(SimpleNamespace(job_queue=queue))
    assert registered == ['morning_report']
    assert 'agent_signals schedule failed (ValueError)' in caplog.text


def test_equal_to_floor_is_quiet(world):
    _update(world, sleep_total=6, readiness=65, stress_high_min=10)
    assert signals.state_signals(DAY) == []


def test_lab_score_cannot_hide_prior_result(world):
    world.db.add_lab_result('2026-04-01', 'HGB', 10, unit='g/L', source='manual:synthetic')
    world.db.add_lab_result('2026-04-19', 'HGB', 20, unit='score', source='manual:synthetic')
    world.db.execute("INSERT INTO lab_monitoring_schedule(test_name,interval_days,source) VALUES ('HGB',30,'manual')")
    signals.run(DAY)
    assert '2026-05-01' in _letters(world, 'lab_due')[0]['body']


def test_config_transaction_rollback(world):
    with pytest.raises(RuntimeError):
        with health_db.get_conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            config_db.upsert_config('synthetic.transaction', value_text='lost', conn=conn)
            raise RuntimeError('rollback')
    assert config_db.get_config('synthetic.transaction') is None


def test_storage_failure_is_not_normal(world):
    world.db.execute('DROP TABLE daily_metrics')
    with pytest.raises(Exception):
        signals.run(DAY)
    assert config_db.get_config('agent_signals.last_morning') is None


def test_recent_labs_optional_window_and_key_filter(world):
    world.db.add_lab_result('2020-01-01', 'HGB', 10, unit='g/L', source='manual:synthetic')
    world.db.add_lab_result('2026-04-21', 'HGB', 11, unit='g/L', source='manual:synthetic')
    world.db.add_lab_result('2026-04-01', 'WBC', 12, unit='g/L', source='manual:synthetic')
    rows = labs_db.get_recent_labs(n_days=None, key_tests=['HGB'], reference_date=DAY)
    assert [(row['test_name'], row['date']) for row in rows] == [('HGB', '2020-01-01')]
    assert labs_db.get_recent_labs(n_days=30, key_tests=['HGB'], reference_date=DAY) == []


def test_regimen_respects_treatment_review_and_active_supplements(world):
    for name, modality, status, confirmation in [
        ('Текущая', 'supplement', 'active', 'confirmed'),
        ('Завершённая', 'supplement', 'completed', 'confirmed'),
        ('Лекарство', 'drug', 'active', 'confirmed'),
        ('Отклонённая', 'supplement', 'active', 'rejected'),
    ]:
        world.db.execute('INSERT INTO medications(name,modality,status,confirmation) VALUES (?,?,?,?)',
                         (name,modality,status,confirmation))
    signals.run(DAY)
    text = _letters(world, 'regimen')[0]['body']
    assert 'Текущая' in text
    assert all(name not in text for name in ('Завершённая','Лекарство','Отклонённая'))


def test_invalid_metric_fails_without_completed_heartbeat(world):
    _update(world, stress_high_min=float('inf'))
    with pytest.raises(ValueError, match='invalid metric'):
        signals.run(DAY)
    assert _letters(world) == []
    assert config_db.get_config('agent_signals.last_morning') is None


@pytest.fixture
def lab_mail_memory(monkeypatch):
    """Exercise real _labs -> _publish -> _blocks; storage/transport are in memory."""
    from contextlib import nullcontext
    from copy import deepcopy
    ledger, letters, rows = {}, [], []
    monkeypatch.setattr(signals, 'policy', lambda: {'lab_lead_days': 14})
    monkeypatch.setattr(signals.profile_db, 'get_patient_profile', lambda: {'identity.language': 'ru'})
    monkeypatch.setattr(labs_db, 'tenant_lab_schedule', lambda: {
        'CEA': {'interval_days': 30, 'source': 'default'}})
    monkeypatch.setattr(labs_db, 'get_recent_labs', lambda **kw: rows)
    monkeypatch.setattr(signals.lab_canon, 'synonyms_for', lambda name: ['РЭА'])
    monkeypatch.setattr(health_db, 'get_conn', lambda: nullcontext(
        SimpleNamespace(execute=lambda sql: None)))
    monkeypatch.setattr(config_db, 'get_config',
                        lambda key, default=None, **kw: deepcopy(ledger.get(key, default)))
    monkeypatch.setattr(config_db, 'upsert_config',
                        lambda key, value_json, **kw: ledger.update({key: deepcopy(value_json)}))
    def send(to, subject, body, **kw):
        letters.append(dict(to=to, subject=subject, body=body, **kw))
        return str(len(letters))
    monkeypatch.setattr(agent_mail, 'send', send)
    return SimpleNamespace(ledger=ledger, letters=letters, rows=rows)


@pytest.mark.parametrize('source,origin', [
    ('encounter:7', 'doctor'), ('manual', 'manual'), ('manual:review', 'manual'),
    ('manually_set', 'manual'), ('default', 'system'), ('code_seed', 'system'),
    ('stable', 'system'), ('encounter', 'system'), ('', 'system'), (None, 'system'),
])
def test_schedule_origin(source, origin):
    assert labs_db.schedule_origin(source) == origin


@pytest.mark.parametrize('source,interval,overdue,label,urgency', [
    ('encounter:7', 30, 31, 'назначение врача (документ)', 'сегодня'),
    ('manual:review', 30, 31, 'вручную, документа нет', 'сегодня'),
    ('default', 30, 31, 'по умолчанию системы', 'при ближайшем заборе'),
    ('default', 60, 31, 'по умолчанию системы', 'сегодня'),
    ('default', 30, 30, 'по умолчанию системы', 'сегодня'),
    ('default', 30, 0, 'по умолчанию системы', 'сегодня'),
    ('default', 30, -1, 'по умолчанию системы', 'на неделе'),
    ('code_seed', 10, 11, 'по умолчанию системы', 'при ближайшем заборе'),
])
def test_lab_letter_origin_urgency_and_acronym(lab_mail_memory, monkeypatch,
                                             source, interval, overdue, label, urgency):
    monkeypatch.setattr(labs_db, 'tenant_lab_schedule', lambda: {
        'CEA': {'interval_days': interval, 'source': source}})
    lab_mail_memory.rows.append({'test_name': 'CEA',
                                'date': str(DAY - timedelta(days=overdue + interval))})
    assert signals._labs('assistant-example', DAY) == '1'
    letter = lab_mail_memory.letters[0]
    assert 'Что случилось: РЭА — срок ' in letter['body']
    assert 'Интервал: ' + label in letter['body']
    assert f'Срочность: {urgency}\n' in letter['body']
    assert letter['kind'] == 'lab_due'


def test_lab_overdue_fingerprint_survives_next_day(lab_mail_memory):
    lab_mail_memory.rows.append({'test_name': 'CEA', 'date': '2026-01-01'})
    assert signals._labs('assistant-example', DAY) == '1'
    assert signals._labs('assistant-example', DAY + timedelta(days=1)) is None
    assert len(lab_mail_memory.letters) == 1
    assert lab_mail_memory.ledger['agent_signals.ledger.assistant-example.lab_due']['tokens'] == ['CEA:2026-01-31']
    lab_mail_memory.rows.append({'test_name': 'CEA', 'date': '2026-01-02'})
    assert signals._labs('assistant-example', DAY + timedelta(days=2)) == '2'
    assert len(lab_mail_memory.letters) == 2


def test_public_install_without_private_file_has_no_assistant(tmp_path, monkeypatch):
    """08.11: is_owner() истинно у любой установки — без приватного справочника адресата нет,
    чужая установка не пишет ассистенту владельца. Мутация «вернуть зашитый адрес» — красная."""
    root = tmp_path / 'health'
    root.mkdir()
    monkeypatch.setenv('HEALTH_DATA_DIR', str(root))
    monkeypatch.setattr(health_db, 'DB_PATH', root / 'health.db')
    monkeypatch.setattr(health_db, 'AGENT_MAIL_PRIVATE', tmp_path / 'нет.yaml')
    health_db._migrate_patient_profile_and_config()
    health_db._migrate_agent_mail()
    assert config_db.get_config('agent_mail.assistant') == ''
