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
    monkeypatch.setattr(w.lab_backfill, "_SHARED_VOCAB_DB", tmp_path / "lab_vocab.db")
    monkeypatch.setattr(w.notify, "_SECRETS", tmp_path / "secrets")
    monkeypatch.setattr(w.notify, "_telegram", lambda *a, **k: True)
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


def test_unadmitted_lab_reading_is_told_in_words_and_resumes(env, monkeypatch):
    """02.10, решение владельца «А»: у DeepSeek чтение анализов не допущено. Документ пришёл —
    его отправитель слышит предел словами (не журнал сбоев, не тишина); документ ждёт и
    разбирается сам, когда чтение станет допущено. Настоящий run_backfill: ревью 02.10 нашло,
    что он глотал отказ, и карточка была мёртвой."""
    import hai_core
    hdb, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    doc = inbox / "lab.pdf"; doc.write_text("b"); _age(doc, 999)
    told, faults, ops = [], [], []

    def refuse(*a, **k):
        raise hai_core.ModelNotAdmitted("deepseek: модель 'deepseek-v4-pro' роли 'opus' не прошла допуск")
    monkeypatch.setattr(w.lab_backfill.lab_recognizer, "recognize", refuse)
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg, **k: told.append(msg) or "telegram")
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: ops.append(msg) or "telegram")
    monkeypatch.setattr(w.notify, "fault", lambda *a, **k: faults.append(a))
    monkeypatch.setattr(w, "_lab_reading_admitted", lambda: False)
    w.process_once()
    w.process_once()                                   # ждёт, не повторяет и не шумит
    assert (inbox / "lab.pdf.notadmitted").exists()
    assert not (inbox / "lab.pdf.failed").exists() and not (inbox / "lab.pdf.norows").exists()
    assert len(told) == 1 and "поставщиком моделей" in told[0]
    assert not faults and not ops, "предел установки — не сбой; канал служебный (01.08)"

    seen = []
    monkeypatch.setattr(w, "_lab_reading_admitted", lambda: True)
    monkeypatch.setattr(w.lab_backfill, "run_backfill",
                        lambda *a, **k: seen.append(a) or {"rows": 3, "pending": 0})
    monkeypatch.setattr(w.notify, "weekly", lambda *a, **k: None)
    w.process_once()
    assert seen and not (inbox / "lab.pdf.notadmitted").exists(), "допуск появился — разобран сам"


def test_unadmitted_unreached_person_leaves_a_fault(env, monkeypatch):
    """Ревью 02.10: Telegram и резерв молчат — маркер стоит, человек не узнал, журнал пуст."""
    import hai_core
    hdb, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    doc = inbox / "lab3.pdf"; doc.write_text("b"); _age(doc, 999)
    faults = []
    monkeypatch.setattr(w.lab_backfill, "run_backfill",
                        lambda *a, **k: (_ for _ in ()).throw(hai_core.ModelNotAdmitted("x")))
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg, **k: "none")
    monkeypatch.setattr(w.notify, "fault", lambda *a, **k: faults.append(a))
    w.process_once()
    assert (inbox / "lab3.pdf.notadmitted").exists() and len(faults) == 1


def test_other_recognition_failure_stays_a_fault(env, monkeypatch):
    hdb, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    other = inbox / "other.pdf"; other.write_text("b"); _age(other, 999)
    told, faults = [], []
    monkeypatch.setattr(w.lab_backfill, "run_backfill",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg, **k: told.append(msg))
    monkeypatch.setattr(w.notify, "fault", lambda *a, **k: faults.append(a))
    w.process_once()
    assert (inbox / "other.pdf.failed").exists() and faults and not told


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


@pytest.mark.parametrize("owner", [True, False])
def test_auto_rows_reach_own_chat_and_stay_out_of_canon(env, monkeypatch, owner):
    """Мутации: notify_operator вместо notify; убрать auto-уведомление; автопромоут."""
    hdb, w, tmp = env
    hdb._ensure_lab_table()
    from secrets_paths import secrets_dir
    import secrets_paths
    monkeypatch.setattr(secrets_paths, "is_owner", lambda: owner)
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "own.jpg"; f.write_bytes(b"synthetic image"); _age(f, 999)
    res = {"date": "2030-01-02", "extractor_version": "synthetic",
           "stats": {"disagreements": 0, "singles": 0},
           "tests": [{"canonical_name": "Glucose", "raw_name": "Glucose", "value": 5.0,
                      "unit": "mmol/L", "page": 1, "value_agreement": "agree"}]}
    monkeypatch.setattr(w.lab_backfill.lab_recognizer, "recognize", lambda *a, **k: res)
    monkeypatch.setattr(w.lab_backfill.lab_oracles, "verify",
                        lambda *a, **k: {"status": "green", "count": 0})
    sent, operators = [], []
    monkeypatch.setattr(w.notify, "_telegram",
                        lambda msg, secrets=None: sent.append((msg, secrets or w.notify._SECRETS)) or True)
    monkeypatch.setattr(w.notify, "notify_operator", lambda msg: operators.append(msg) or "telegram")
    assert w.process_once() == 1
    assert w.process_once() == 0
    assert len(sent) == 1 and sent[0][1] == secrets_dir()
    assert "own.jpg" in sent[0][0] and "Добавить в базу" in sent[0][0]
    assert "/lab-review/" in sent[0][0] and "tenant=health_partner" in sent[0][0]
    assert "show=waiting" in sent[0][0]
    assert len(operators) == (0 if owner else 1)
    with hdb.get_conn() as c:
        assert c.execute("SELECT review_status FROM lab_results_staging").fetchall()[0][0] == "auto"
        assert c.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0] == 0


@pytest.mark.parametrize("case", ["notlab", "zero", "read_failure", "failure"])
def test_file_refusal_reaches_person_once(env, monkeypatch, case):
    """Мутации: убрать личное уведомление либо отключить notlab/.norows/.failed дедуп."""
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / f"{case}.jpg"; f.write_bytes(b"invalid image"); _age(f, 999)
    told = []
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    if case == "notlab":
        monkeypatch.setattr(w, "_is_lab", lambda p: False)
    elif case == "zero":
        monkeypatch.setattr(w.lab_backfill, "run_backfill", lambda *a, **k: {"rows": 0})
    else:
        def fail(*a, **k):
            raise ValueError("synthetic read failure")
        if case == "read_failure":
            monkeypatch.setattr(w.lab_backfill.lab_recognizer, "recognize", fail)
        else:
            monkeypatch.setattr(w.lab_backfill, "run_backfill", fail)
    for _ in range(3):
        w.process_once()
    assert len(told) == 1 and f.name in told[0]
    # Три разных правды (нить lab-intake-retry, 05.10). До неё все четыре случая звучали
    # «пришлите почётче» — и на «не бланк», и на сбой у нас, где файл ни при чём.
    if case == "notlab":
        assert "не похож на бланк" in told[0] and "ещё раз" in told[0]
    elif case == "zero":
        assert "оригинальный PDF" in told[0] and "чёткое фото" in told[0]
    else:
        assert "на моей стороне" in told[0] and "чёткое фото" not in told[0]


def test_long_pdf_refused_once_without_partial_staging(env, monkeypatch):
    """Настоящие PDF → recognizer → backfill → watcher. Мутации: усечь или проглотить отказ."""
    hdb, w, _ = env
    import fitz
    lr = w.lab_backfill.lab_recognizer
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / "long.pdf"
    with fitz.open() as doc:
        for _ in range(lr._MAX_PAGES + 1):
            doc.new_page()
        doc.save(str(p))
    _age(p, 999)
    monkeypatch.setattr(lr.hai_core, "get_model", lambda role: role)
    vision, told = [], []
    monkeypatch.setattr(lr, "_vision_call", lambda *a: vision.append(a) or [])
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    w.process_once(); w.process_once(); w.process_once()
    assert len(told) == 1 and "Разделите PDF" in told[0] and str(lr._MAX_PAGES) in told[0]
    assert not vision and p.with_suffix(".pdf.failed").exists()
    with hdb.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM lab_results_staging").fetchone()[0] == 0


def test_undelivered_person_result_leaves_fault_without_retry_spam(env, monkeypatch):
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "notlab.jpg"; f.write_bytes(b"x"); _age(f, 999)
    monkeypatch.setattr(w, "_is_lab", lambda p: False)
    attempts, faults = [], []
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: attempts.append(k) or "none")
    monkeypatch.setattr(w.notify, "fault", lambda *a, **k: faults.append(a))
    w.process_once(); w.process_once()
    assert attempts == [{"fallback": False}] and len(faults) == 1


def test_notlab_notification_waits_for_durable_dedup(env, monkeypatch):
    """Мутация: сообщать при неудачной записи notlab, создавая шторм по минутам."""
    _, w, _ = env
    from pathlib import Path
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "notlab.jpg"; f.write_bytes(b"x"); _age(f, 999)
    monkeypatch.setattr(w, "_is_lab", lambda p: False)
    original = Path.write_text
    blocked = True
    def write(path, *a, **k):
        if blocked and path == w._state_path():
            raise OSError("synthetic full disk")
        return original(path, *a, **k)
    monkeypatch.setattr(Path, "write_text", write)
    told = []
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    w.process_once(); w.process_once()
    assert not told
    blocked = False
    w.process_once(); w.process_once()
    assert len(told) == 1


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


# ── нить lab-intake-retry (05.10): пустой ключ, повтор после отказа ──────────────

class _Quota(Exception):
    """Как openai.RateLimitError на пустом балансе: 429 и код insufficient_quota в тексте."""
    status_code = 429


def test_empty_key_waits_tells_once_and_resumes(env, monkeypatch):
    """Мутации: проглотить отказ в «0 строк» (lab_backfill), писать .failed, говорить каждый повтор,
    не повторять после пополнения. Отказ идёт из распознавателя — через настоящий run_backfill."""
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "lab__0f1e2d3c4b5a6978.pdf"; f.write_bytes(b"%PDF"); _age(f, 999)
    told = []
    monkeypatch.setattr(w.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    def empty_key(*a, **k):
        raise _Quota("Error code: 429 - {'error': {'code': 'insufficient_quota'}}")
    monkeypatch.setattr(w.lab_backfill.lab_recognizer, "recognize", empty_key)
    w.process_once()
    wk = inbox / "lab__0f1e2d3c4b5a6978.pdf.waitkey"
    assert wk.exists() and not (inbox / "lab__0f1e2d3c4b5a6978.pdf.failed").exists()
    assert not (inbox / "lab__0f1e2d3c4b5a6978.pdf.norows").exists()
    assert len(told) == 1 and "баланс" in told[0] and "«lab.pdf»" in told[0]   # имя без хеша
    w.process_once()                       # рано: повтора нет
    _age(wk, w.KEY_RETRY_SEC + 1)
    w.process_once()                       # повтор, ключ всё ещё пуст — молча
    assert len(told) == 1 and wk.exists()
    _age(wk, w.KEY_RETRY_SEC + 1)
    monkeypatch.setattr(w.lab_backfill, "run_backfill", lambda *a, **k: {"rows": 0})
    w.process_once()                       # баланс пополнен: файл разобран без новой присылки
    assert not wk.exists() and (inbox / "lab__0f1e2d3c4b5a6978.pdf.norows").exists()


def test_resend_after_refusal_requeues_and_clean_duplicate_does_not(env, monkeypatch):
    """Мутации: не снимать сайдкар; оставить файл в notlab (гейт снова отвергнет); считать повтор
    разобранного файла отказом."""
    _, w, _ = env
    inbox = w._incoming(); inbox.mkdir(parents=True, exist_ok=True)
    failed = inbox / "a.pdf"; failed.write_bytes(b"x")
    (inbox / "a.pdf.failed").write_text("boom")
    (inbox / "a.pdf.events.failed").write_text("boom")
    notlab = inbox / "b.pdf"; notlab.write_bytes(b"y")
    w._save_state({"watermark": 0.0, "notlab": ["b.pdf"]})
    clean = inbox / "c.pdf"; clean.write_bytes(b"z")
    assert w.retry_after_refusal(failed) is True
    assert not (inbox / "a.pdf.failed").exists() and not (inbox / "a.pdf.events.failed").exists()
    assert w.retry_after_refusal(notlab) is True
    st = w._load_state()
    assert "b.pdf" not in st["notlab"] and "b.pdf" in st["forced"]
    assert w.retry_after_refusal(clean) is False


def test_bot_duplicate_points_at_the_stored_file(tmp_path):
    """Дубль обязан назвать лежащий файл, а не выдуманный `dup__…`: по нему ищется отказ."""
    import hashlib
    from handlers.messages import _inbox_dest
    data = b"same bytes"
    h = hashlib.sha256(data).hexdigest()[:16]
    stored = tmp_path / f"report__{h}.pdf"; stored.write_bytes(data)
    (tmp_path / f"report__{h}.pdf.failed").write_text("x")
    dest, dup = _inbox_dest(tmp_path, "renamed.pdf", data)
    assert dup and dest == stored
