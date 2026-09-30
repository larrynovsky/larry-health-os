"""Тесты watcher авто-распознавания инбокса (lab_intake_watcher).

Закрывает дыру: watcher раньше не имел автотестов. Проверяем ЛОГИКУ без API:
tenant/inbox-резолв, дебаунс (_stable), идемпотентность (пропуск уже-в-staging),
доставку уведомления ревьюеру. Сам вызов распознавания замокан.
"""
import os
import time
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    # Тенант задан, значит secrets_dir обязан быть задан явно — иначе он отказывает,
    # и правильно делает (взял бы секреты владельца, это кросс-тенант утечка).
    # Раньше файл проходил на удаче порядка импортов; после 2026-07-29 фикстура
    # трогает состояние на диске раньше, и удача кончилась.
    (tmp_path / "secrets").mkdir(parents=True)
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path / "secrets"))
    (tmp_path / "health_partner" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health_partner" / "data" / "health.db")
    health_db.init_db()
    w = pytest.importorskip("lab_intake_watcher")   # тянет lab_backfill/recognizer
    # Два предохранителя, добавленные 2026-07-29 (гейт классификатора и водяной знак),
    # здесь СНЯТЫ намеренно: этот файл проверяет механику диспетчеризации — кого берём,
    # кого пропускаем, что делаем с нулём строк. Не сними их, и все проверки ниже
    # зеленели бы по неверной причине («ничего не взяли»), то есть перестали бы
    # что-либо доказывать. Сами предохранители проверяются отдельно и по одному:
    # tests/unit/test_lab_intake_gate.py.
    monkeypatch.setattr(w, "_is_lab", lambda p: True)
    w._save_state({"watermark": 0.0, "notlab": []})
    return health_db, w, tmp_path


def _age(f, seconds):
    old = time.time() - seconds
    os.utime(f, (old, old))


def test_tenant_and_inbox(env):
    _, w, tmp = env
    assert w._tenant() == "health_partner"
    assert w._incoming() == tmp / "health_partner" / "incoming"


def test_stable_debounce(env):
    _, w, _ = env
    d = w._incoming(); d.mkdir(parents=True, exist_ok=True)
    f = d / "x.pdf"; f.write_text("x")
    assert w._stable(f) is False           # только что записан
    _age(f, 999)
    assert w._stable(f) is True            # устоялся


def test_process_once_skips_processed_processes_new(env, monkeypatch):
    hdb, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    done = inbox / "done.pdf"; done.write_text("d")
    new = inbox / "new.pdf"; new.write_text("n")
    _age(done, 999); _age(new, 999)
    # done.pdf уже в staging (по basename) → пропуск
    with hdb.get_conn() as c:
        c.execute("INSERT INTO lab_results_staging(run_id, extractor_version, source_file, date) "
                  "VALUES('r','v','/x/done.pdf','2025-01-01')")
        c.commit()

    def fake_backfill(run_id, *a, **k):
        return {"rows": 3, "pending": 1}
    notes = []
    monkeypatch.setattr(w.lab_backfill, "run_backfill", fake_backfill)
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg: notes.append(msg) or "telegram")

    n = w.process_once()
    assert n == 1                                  # обработан только new.pdf
    assert notes and "Сверить их с бланком" in notes[0] and "new.pdf" not in notes[0]
    assert "lab-review" in notes[0]                # ссылка на ревью
    assert "done.pdf" not in "".join(notes)        # done пропущен


def test_process_once_failed_marker_no_retry(env, monkeypatch):
    hdb, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    bad = inbox / "bad.pdf"; bad.write_text("b"); _age(bad, 999)

    def boom(*a, **k):
        raise RuntimeError("recognize failed")
    monkeypatch.setattr(w.lab_backfill, "run_backfill", boom)
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg: "telegram")

    w.process_once()
    assert (inbox / "bad.pdf.failed").exists()     # маркер выставлен
    # второй проход не трогает .failed
    calls = []
    monkeypatch.setattr(w.lab_backfill, "run_backfill", lambda *a, **k: calls.append(1) or {})
    assert w.process_once() == 0
    assert not calls


# ── 2026-07-28: сигнал расхождения «принят как таблица, распознано 0 строк» ────

def test_zero_rows_is_discrepancy_not_success(env, monkeypatch):
    """Молчаливое «0 строк, 0 на ревью» читалось бы как «документ пустой» — а это
    ровно то, как выглядит промах классификации. Сигнал обязан звучать иначе."""
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "notatable.jpg"; f.write_text("x"); _age(f, 999)
    notes = []
    monkeypatch.setattr(w.lab_backfill, "run_backfill", lambda *a, **k: {"rows": 0, "pending": 0})
    monkeypatch.setattr(w.notify, "fault", lambda msg, person_key=None: notes.append(msg))

    assert w.process_once() == 1
    assert notes and "no rows recognized" in notes[0]
    assert "распознан:" not in notes[0]          # не выдаём промах за успех


def test_zero_rows_marks_sidecar_and_stops_repeating(env, monkeypatch):
    """Без сайдкара basename не попадает в staging → файл берётся КАЖДЫЙ поллинг,
    и сигнал превращается в шторм раз в минуту. Контроль стережёт именно это."""
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "notatable.jpg"; f.write_text("x"); _age(f, 999)
    calls, notes = [], []
    monkeypatch.setattr(w.lab_backfill, "run_backfill",
                        lambda *a, **k: calls.append(1) or {"rows": 0, "pending": 0})
    monkeypatch.setattr(w.notify, "fault", lambda msg, person_key=None: notes.append(msg))

    w.process_once()
    assert f.with_suffix(f.suffix + ".norows").exists()
    w.process_once()                              # второй поллинг
    w.process_once()                              # третий
    assert len(calls) == 1 and len(notes) == 1    # распознавание и сигнал ровно однажды


def test_subdirs_are_not_picked_by_watcher(env, monkeypatch):
    """imaging/ и reports/ — не для табличного распознавателя."""
    _, w, _ = env
    inbox = w._incoming()
    (inbox / "imaging").mkdir(parents=True, exist_ok=True)
    img = inbox / "imaging" / "uzi.jpg"; img.write_text("x"); _age(img, 999)
    calls = []
    monkeypatch.setattr(w.lab_backfill, "run_backfill",
                        lambda *a, **k: calls.append(1) or {"rows": 1, "pending": 1})
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg: None)
    assert w.process_once() == 0 and calls == []


# --- периметр датчика пульса (§18) -------------------------------------------
# Регрессия 2026-08-01: датчик искал джобу вотчера ПО ИМЕНИ («lab-intake») и не
# видел партнёрскую `com.larry.health.watcher.partner`, исполнявшую тот же модуль.
# Трое суток спама при зелёном датчике. Оракул — на чистом ядре, потому что сам
# обход launchd Studio-only и на тест-хосте не исполняется.

def _plist(args, data_dir=None):
    d = {"ProgramArguments": args}
    if data_dir:
        d["EnvironmentVariables"] = {"HEALTH_DATA_DIR": data_dir}
    return d


def test_perimeter_finds_job_whose_name_says_nothing():
    """ПОЗИТИВ: имя джобы не содержит «lab-intake», но она исполняет модуль."""
    import lab_intake_watcher as w
    jobs = w.select_intake_jobs(
        [("com.larry.health.watcher.partner",
          _plist(["/opt/homebrew/bin/python3.11",
                  "/Users/zz/health_scripts/lab_intake_watcher.py"],
                 "/Users/zz/health_partner"))],
        "lab_intake_watcher.py")
    assert [lbl for lbl, _ in jobs] == ["com.larry.health.watcher.partner"]
    assert jobs[0][1] == w.heartbeat_for("/Users/zz/health_partner")


def test_perimeter_ignores_job_named_like_us_but_running_other_code():
    """НЕГАТИВ: имя похоже, исполняет чужой модуль → не наш тенант."""
    import lab_intake_watcher as w
    assert w.select_intake_jobs(
        [("com.larry.health.lab-intake-report",
          _plist(["python3.11", "/Users/zz/health_scripts/lab_backfill.py"],
                 "/Users/zz/health"))],
        "lab_intake_watcher.py") == []


def test_perimeter_skips_job_without_tenant():
    """Без HEALTH_DATA_DIR пульс не адресуем — джоба пропускается, а не гадаем."""
    import lab_intake_watcher as w
    assert w.select_intake_jobs(
        [("com.larry.health.lab-intake",
          _plist(["python3.11", "/Users/zz/health_scripts/lab_intake_watcher.py"]))],
        "lab_intake_watcher.py") == []


@pytest.fixture(autouse=True)
def _no_side_routes(monkeypatch):
    """process_once зовёт ещё два маршрута входящих (геном, заключения — document-intake 24.09).
    Здесь судится разбор АНАЛИЗОВ: соседние маршруты глушатся, чтобы тест не тянул модель
    и не слал человеку «не смог разобрать» по фиктивным файлам. У них свои тесты."""
    import genome_intake, import_medical_events
    monkeypatch.setattr(genome_intake, "process_pending", lambda *a, **k: 0)
    monkeypatch.setattr(import_medical_events, "process_incoming", lambda *a, **k: 0)
