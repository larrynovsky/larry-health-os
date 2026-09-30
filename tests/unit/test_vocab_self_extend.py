"""Тесты общего самопополняющегося словаря аналитов (lab_backfill vocab).

Закрывает дыру: #2b (self-extend) раньше был только smoke.
_load_vocab = базовый _VOCAB ∪ ОБЩИЙ lab_vocab; add_vocab пишет (idempotent);
apply_assignment обновляет staging + добавляет в общий словарь.
Общий стор изолируем в tmp (не реальный ~/.health_shared).
"""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    try:
        import lab_backfill
    except Exception as e:  # тяжёлые зависимости recognizer'а отсутствуют
        pytest.skip(f"lab_backfill import: {e}")
    monkeypatch.setattr(lab_backfill, "_SHARED_VOCAB_DB", tmp_path / "shared" / "lab_vocab.db")
    return health_db, lab_backfill


def test_load_vocab_has_base_and_seeded(env):
    _, lb = env
    v = lb._load_vocab()
    assert "Glucose" in v          # база
    assert "AFP" in v              # досеянный онкомаркер
    assert "Urine_pH" in v         # досеянная мочевая панель


def test_add_vocab_extends_and_idempotent(env):
    _, lb = env
    assert "ZZZ_TEST" not in lb._load_vocab()
    assert lb.add_vocab("ZZZ_TEST") is True
    assert "ZZZ_TEST" in lb._load_vocab()
    assert lb.add_vocab("ZZZ_TEST") is True          # повтор — без дублей
    assert lb._load_vocab().count("ZZZ_TEST") == 1
    assert lb.add_vocab("") is False                 # пустое не добавляем


def test_apply_assignment_updates_staging_and_vocab(env):
    hdb, lb = env
    with hdb.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,"
                "canonical_name,raw_name,value,value_agreement,review_status")
        c.execute(f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ("rX", "v", "doc.pdf", "2025-05-28", "urine",
                   None, "YENI_ANALIT", 1.5, "single", "pending"))
        c.commit()
    n = lb.apply_assignment("rX", "doc.pdf", "YENI_ANALIT", 1.5, "Urine_Newthing")
    assert n == 1
    with hdb.get_conn() as c:
        row = c.execute(
            "SELECT canonical_name FROM lab_results_staging WHERE raw_name='YENI_ANALIT'"
        ).fetchone()
    assert row[0] == "Urine_Newthing"                # staging обновлён
    assert "Urine_Newthing" in lb._load_vocab()      # добавлен в общий словарь
