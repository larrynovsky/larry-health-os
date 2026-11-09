"""assessment_scheduler — idempotency + создание задач (SX-17g)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup_instrument_catalog(tmp_path, monkeypatch):
    """Создать каталог инструментов в tmp и подменить путь в модуле."""
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "isi.json").write_text(json.dumps({
        "id": "isi", "name": "ISI", "cadence_days": 90, "cadence_offset_days": 0,
        "items": [{"id": "q1", "text": "?", "subscale": "total"}],
    }, ensure_ascii=False))
    import assessment_scheduler as sched
    monkeypatch.setattr(sched, "INSTRUMENTS_DIR", cat)
    monkeypatch.setattr(sched, "BUNDLED_INSTRUMENTS_DIR", cat)
    return sched


def test_scheduler_creates_task_when_no_filling(db, tmp_path, monkeypatch):
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    created = sched.run()
    assert len(created) == 1
    assert created[0]["instrument"] == "isi"
    # Подтвердим в БД
    rows = db.fetchall("SELECT fingerprint, content FROM tasks WHERE source='assessment_scheduler'")
    assert any(r["fingerprint"] == "assessment:isi" for r in rows)
    assert "Вопросы о бессоннице" in rows[0]["content"]
    assert "за последнюю неделю" in rows[0]["content"]
    assert "1 вопрос" in rows[0]["content"]
    assert "recall" not in rows[0]["content"] and "ISI" not in rows[0]["content"]


def test_scheduler_is_idempotent_no_duplicate(db, tmp_path, monkeypatch):
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    first = sched.run()
    second = sched.run()
    assert len(first) == 1
    assert len(second) == 0  # idempotent: open task → skip


def test_bundled_catalog_survives_empty_tenant_and_overrides(db, tmp_path, monkeypatch):
    """A: real readers and producer, synthetic filesystem and in-memory DB."""
    import assessment_importer as importer
    import assessment_dialog as dialog
    import survivorship_analyzer as analyzer
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    tenant = tmp_path / 'empty_tenant'
    tenant.mkdir()
    for module in (sched, importer, dialog, analyzer):
        monkeypatch.setattr(module, 'INSTRUMENTS_DIR', tenant)
    original = sched._load_instruments()[0]
    assert importer._load_instrument('isi') == original
    assert dialog._load_instrument_by_source_id('isi') == original
    assert analyzer._load_instruments() == [original]
    assert len(sched.run()) == 1
    override = dict(original, name='Synthetic override', cadence_days=12)
    (tenant / 'isi.json').write_text(json.dumps(override))
    assert sched._load_instruments() == [override]
    assert importer._load_instrument('isi') == override
    assert dialog._load_instrument_by_source_id('isi') == override
    (tenant / 'isi.json').write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        sched._load_instruments()
    (tenant / 'isi.json').unlink()
    (sched.BUNDLED_INSTRUMENTS_DIR / 'isi.json').unlink()
    with pytest.raises(FileNotFoundError):
        sched.run()


def test_producer_liveness_red_and_interval_boundary(db, tmp_path, monkeypatch):
    """B: execute registered sensor without importing the whole nightly monitor."""
    import ast
    from datetime import date, timedelta
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    policy = tmp_path / 'schedule.json'
    policy.write_text(json.dumps({'cadence_days': 90, 'grace_days': 3}))
    monkeypatch.setattr(sched, 'SCHEDULE_POLICY_PATH', policy)
    ins = json.loads((sched.INSTRUMENTS_DIR / 'isi.json').read_text())
    ins['cadence_days'] = 11
    (sched.INSTRUMENTS_DIR / 'isi.json').write_text(json.dumps(ins))
    today = date(2026, 11, 7)
    monkeypatch.setattr(sched, 'get_today', lambda: today)
    check = _producer_check(sched, today)
    with pytest.raises(AssertionError, match='isi'):
        check()  # never produced
    assert len(sched.run()) == 1
    check()  # open task inhibits production, even if very old
    db.execute("UPDATE tasks SET created_at='2020-01-01' WHERE fingerprint='assessment:isi'")
    check()
    db.execute("UPDATE tasks SET status='done', created_at=? WHERE fingerprint='assessment:isi'",
               ((today - timedelta(days=14)).isoformat(),))
    check()  # exactly cadence + grace
    db.execute("UPDATE tasks SET created_at=? WHERE fingerprint='assessment:isi'",
               ((today - timedelta(days=15)).isoformat(),))
    with pytest.raises(AssertionError, match='isi'):
        check()
    db.execute("INSERT INTO lab_results (date,source,test_name,value,unit) VALUES (?, 'instrument:isi','isi_total',1,'score')",
               ((today - timedelta(days=14)).isoformat(),))
    check()  # recent answer also counts
    db.execute("UPDATE lab_results SET date='invalid' WHERE source='instrument:isi'")
    with pytest.raises(ValueError):
        check()
    (sched.INSTRUMENTS_DIR / 'isi.json').unlink()
    with pytest.raises(FileNotFoundError):
        check()


def test_recurring_templates_ship_in_image(db, monkeypatch, tmp_path):
    """Distribution oracle: committed templates, not a developer's tenant directory."""
    from fnmatch import fnmatch
    from scripts.install import render_dockerignore
    import assessment_scheduler as sched
    monkeypatch.setattr(sched, 'INSTRUMENTS_DIR', tmp_path / 'no_tenant_catalog')
    instruments = sched.load_instruments()
    assert {i['id']: len(i['items']) for i in instruments} == {
        'isi': 7, 'mfsi_sf': 30}
    assert all(i['validation_status'] == 'paraphrase' for i in instruments)
    assert all(i['applies_when'] == ['oncology'] for i in instruments)
    db.execute('DELETE FROM clinical_kb_conditions')
    assert sched.run() == []
    from datetime import date
    _producer_check(sched, date(2026, 11, 7))()
    root = Path(__file__).resolve().parents[2]
    paths = [sched.SCHEDULE_POLICY_PATH, *sched.BUNDLED_INSTRUMENTS_DIR.glob('*.json')]
    patterns = [p for p in render_dockerignore().splitlines() if p and not p.startswith('#')]
    for path in paths:
        relative = path.relative_to(root)
        assert not any(fnmatch(str(part), pattern) for part in [relative, *relative.parents]
                       for pattern in patterns)


def _producer_check(sched, today):
    import ast
    from datetime import date
    tree = ast.parse((Path(__file__).resolve().parents[2] / 'integrity_tests.py').read_text())
    names = {'_sx_instruments_iter', 'check_assessment_producer_liveness'}
    registrations = []
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    nodes += [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and isinstance(n.value.func, ast.Name) and n.value.func.id == 'check'
              and any(isinstance(a, ast.Name) and a.id == 'check_assessment_producer_liveness'
                      for a in n.value.args)]
    env = dict(db=sched.db, date=date, get_today=lambda: today,
               SX_INSTRUMENTS_DIR=sched.INSTRUMENTS_DIR,
               check=lambda label, fn: registrations.append(fn))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'integrity_tests.py', 'exec'), env)
    assert len(registrations) == 1
    return registrations[0]


@pytest.mark.parametrize('conditions,overlay,expected', [
    ([], False, set()),
    (['demo_condition_b'], False, set()),
    (['demo_condition_a'], False, set()),
    (['demo_condition_a', 'demo_condition_b'], False, {'demo_instrument'}),
    ([], True, {'demo_instrument'}),
])
def test_applicability_gates_producer_and_monitor(db, tmp_path, monkeypatch, conditions, overlay, expected):
    """Real clinical_kb resolver; invented Example instrument, conditions and tenant data."""
    import shutil
    from datetime import date
    import assessment_scheduler as sched
    import assessment_importer as importer
    import survivorship_analyzer as analyzer
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'demo_instrument.json').write_text(json.dumps({
        'id': 'demo_instrument', 'name': 'Example questionnaire', 'items': [],
        'applies_when': ['demo_condition_a', 'demo_condition_b'], 'cadence_days': 12}))
    tenant = tmp_path / 'tenant'
    monkeypatch.setattr(sched, 'BUNDLED_INSTRUMENTS_DIR', bundle)
    monkeypatch.setattr(sched, 'INSTRUMENTS_DIR', tenant)
    monkeypatch.setattr(analyzer, 'INSTRUMENTS_DIR', tenant)
    db.execute('DELETE FROM clinical_kb_conditions')
    for cid in ('demo_condition_a', 'demo_condition_b'):
        db.execute('INSERT INTO clinical_kb_conditions (id,trigger_json) VALUES (?,?)',
                   (cid, json.dumps({'problem_regex': 'synthetic_' + cid})))
    for cid in conditions:
        db.execute("INSERT INTO problem_list (problem_id,title,first_seen,last_updated) VALUES (?,?,'2026-11-07','2026-11-07')", (cid, 'synthetic_' + cid))
    if overlay:
        tenant.mkdir()
        shutil.copyfile(bundle / 'demo_instrument.json', tenant / 'demo_instrument.json')
    check = _producer_check(sched, date(2026, 11, 7))
    if expected:
        with pytest.raises(AssertionError):
            check()
    else:
        check()  # no programme is not a dead producer
    assert {importer.instrument_source_id(i['id']) for i in analyzer._load_instruments()} == expected
    created = sched.run()
    assert {r['instrument'] for r in created} == expected
    assert len(db.fetchall("SELECT id FROM tasks WHERE source='assessment_scheduler'")) == len(expected)
    check()  # same filtered programme, now covered by tasks
    # Historical responses remain interpretable even when no longer auto-assigned.
    assert sched.load_instrument('demo_instrument', tenant)['id'] == 'demo_instrument'


@pytest.mark.parametrize('request_text', ['Заполнить DEMO-X', 'Повтори анкету Demo title',
                                      'Пройти Пример анкеты', 'Complete Example survey'])
def test_report_request_uses_scheduler_writer(tmp_path, monkeypatch, caplog, request_text):
    """Invented catalogue and in-memory Python task store; no DB/network/LLM."""
    import types
    from datetime import date
    import assessment_scheduler as sched
    import task_agent as agent
    import task_guard
    import health_db
    import clinical_kb
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    instrument = dict(id='demo_x', name={'en': 'Example survey'}, title='Demo title',
                      title_ru='Пример анкеты', items=[], applies_when=['demo_condition'])
    (bundle / 'demo.json').write_text(json.dumps(instrument))
    monkeypatch.setattr(sched, 'BUNDLED_INSTRUMENTS_DIR', bundle)
    monkeypatch.setattr(sched, 'INSTRUMENTS_DIR', tmp_path / 'tenant')
    rows = []
    conditions = {'demo_condition'}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params):
            assert "status IN ('open', 'snoozed')" in sql
            return types.SimpleNamespace(fetchone=lambda: {'n': sum(
                r['fingerprint'] == params[0] and r['status'] in ('open', 'snoozed') for r in rows)})
    monkeypatch.setattr(health_db, 'get_conn', Connection)
    monkeypatch.setattr(clinical_kb, 'active_conditions', lambda conn: conditions)
    monkeypatch.setattr(health_db, 'init_db', lambda: None)
    monkeypatch.setattr(health_db, 'get_tasks_known_to_person', lambda *a: [])
    def save(**task):
        rows.append(dict(task, id=len(rows) + 1, status='open'))
        return len(rows)
    monkeypatch.setattr(health_db, 'save_task', save)
    monkeypatch.setattr(agent, '_get_client', lambda: types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kw: types.SimpleNamespace(stop_reason='end_turn'))))
    monkeypatch.setattr(agent.llm_client, 'answer_text', lambda response: '[]')
    monkeypatch.setattr(agent.hai_core, 'get_model', lambda role: 'synthetic')
    monkeypatch.setattr(agent.hai_core, 'answer_language', lambda: '')
    monkeypatch.setattr(agent, 'dismissed_recall_days', lambda: 1)
    monkeypatch.setattr(agent, 'judge_task_duplicates', lambda *a: {})
    monkeypatch.setattr(task_guard, 'start_batch', lambda: None)
    monkeypatch.setattr(agent, '_shadow_task_guard', lambda *a: [])
    monkeypatch.setattr(agent, '_parse_tasks_json', lambda raw: [dict(
        type='action', content=request_text, deadline='2040-02-01', reason='synthetic')])
    def extract():
        return agent.extract_tasks_from_report('synthetic', 'gp_weekly', report_date=date(2040, 1, 1))
    saved = extract()
    assert len(saved) == len(rows) == 1
    assert saved[0]['type'] == rows[0]['type_'] == 'assessment'
    assert rows[0]['fingerprint'] == 'assessment:demo_x'
    import assessment_dialog as dialog
    monkeypatch.setattr(dialog, 'REPO_INSTRUMENTS_DIR', tmp_path / 'one_shot')
    monkeypatch.setattr(dialog, 'INSTRUMENTS_DIR', tmp_path / 'tenant')
    assert dialog._instrument_from_fingerprint(saved[0]['fingerprint']) == ('demo_x', instrument)
    assert rows[0]['deadline'] == '2040-02-01' and rows[0]['source'] == 'gp_weekly'
    assert rows[0]['content'] != request_text  # actual questionnaire intro, not a text reminder
    assert extract() == [] and len(rows) == 1
    rows[0]['status'] = 'snoozed'
    assert extract() == [] and len(rows) == 1
    # Scheduler reuses the same writer and honours an existing report task.
    monkeypatch.setattr(sched, '_last_filled', lambda sid: None)
    monkeypatch.setattr(sched, 'schedule_policy', lambda ins: (30, 2))
    assert sched.run() == []
    rows[0]['status'] = 'completed'
    assert len(sched.run()) == 1
    assert rows[1]['source'] == 'assessment_scheduler' and rows[1]['fingerprint'] == 'assessment:demo_x'
    # applies_when rejects another tenant: preserve original text rather than dropping it.
    conditions.clear()
    saved = extract()
    assert saved[0]['type'] == 'action' and rows[-1]['content'] == request_text
    assert 'not applicable' in caplog.text
    # Broken catalogue must not let a model-supplied internal marker create a form.
    (bundle / 'demo.json').write_text('{broken')
    monkeypatch.setattr(agent, '_parse_tasks_json', lambda raw: [dict(
        type='action', content='Arrange a visit', _assessment=instrument)])
    saved = extract()
    assert saved[0]['type'] == 'action' and rows[-1]['content'] == 'Arrange a visit'
    assert 'catalogue unavailable' in caplog.text


@pytest.mark.parametrize('text', ['Обсудить результаты DEMO-X', 'Не заполнять DEMO-X',
                                  'Заполнить DEMO-XX', 'Повторить неизвестную анкету',
                                  'Заполнить DEMO-X и DEMO-Y'])
def test_report_does_not_convert_mentions_negations_or_ambiguity(text):
    from task_agent import _assessment_requested
    assert _assessment_requested(text, [{'id': 'demo_x'}, {'id': 'demo_y'}]) is None
