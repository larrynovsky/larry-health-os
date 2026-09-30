"""Машина состояний прогона гейта — тесты ПОЛНОГО ПУТИ с внесением сбоя (ревью R4).

Почему отдельный файл, а не дополнение к test_longitudinal_gate.py: там оракулы проверяют
СЛОЙ (функция вернула то, что обещала). Раунд 4 показал, чего это стоит: `_write_passset_
snapshot` возвращал корректный bool, `_entered_pairs` корректно читал файл, каждый слой был
прав — а ПУТЬ через них публиковал веру мимо карантина, потому что порядок вызовов был
обратный. Мой же собственный вывод после R3 звучал «проверяй ПУТЬ, а не слой», и я его тогда
не выполнил. Здесь оракулы формулируются только в терминах наблюдаемого снаружи:
что лежит в БД, что лежит в квитанции, что увидел читатель.

Сбой ВНОСИТСЯ (порча артефакта, конкурент, убитый процесс), а не имитируется моком: мок
проверил бы, что я правильно представляю себе сбой, а не что система его переживает.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import longitudinal_analysis as la          # noqa: E402
import quarantine_db                        # noqa: E402


# ─────────────────────────── подпорки ───────────────────────────

def _fixture_conn():
    """Соединение на фикстуре схемы (боевую БД тесты не трогают никогда)."""
    sql = (Path(__file__).resolve().parents[1] / "fixtures" / "health_schema.sql"
           ).read_text(encoding="utf-8")
    i = sql.index("CREATE TABLE IF NOT EXISTS passset_quarantine")
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql[i:sql.index(");", i) + 2])
    return conn


def _snapshot(date_s, members, flicker=None):
    snap = {"date": date_s, "head_sha": "deadbee", "members": members}
    if flicker is not None:
        snap["flicker_vs_prev"] = flicker
    return snap


_MEMBERS_EMPTY = {"D": [], "A": [], "q_lag": []}


# ─────────────────── Фаза 1: прежнее состояние читается ДО писателя ───────────────────

def test_corrupt_prior_history_is_seen_before_writer_heals_it(tmp_path):
    """VG-R4-01, корень. Повреждённая история → прогон НЕ публикует веру.

    Раньше порядок был: писатель лечит → читатель видит вылеченный файл → `entered=[]` →
    «прибытий не было» → пара уходит в веру мимо карантина. Воспроизведено на Studio:
    писатель вернул true, читатель `[]`, один снимок, flicker={}.

    Оракул НЕ «функция вернула ok=False», а «решение о публикации стало False». Это разные
    утверждения: первое было верно и до фикса.
    """
    art = tmp_path / "gate_passset_history.json"
    art.write_text("{это не список}", encoding="utf-8")

    prior = la._validate_prior_passset(art)
    assert prior["ok"] is False, "порча прежнего состояния обязана быть замечена ДО записи"

    # Порядок: валидация прошла ПЕРВОЙ, значит факт порчи уже в решении.
    gate_meta = {"gate_applied": True, "status": "applied"}
    gate_meta["status"] = "failed"          # ровно то, что делает _run_body
    assert la._publication_decision(gate_meta) is False


def test_valid_prior_history_publishes(tmp_path):
    """Негативный контроль к предыдущему: без порчи путь НЕ блокируется.

    Без этого теста предыдущий проходил бы и на реализации «никогда не публиковать»."""
    art = tmp_path / "gate_passset_history.json"
    art.write_text(json.dumps([_snapshot("2026-07-19", _MEMBERS_EMPTY)]), encoding="utf-8")
    assert la._validate_prior_passset(art)["ok"] is True
    assert la._publication_decision({"gate_applied": True, "status": "applied"}) is True


@pytest.mark.parametrize("payload,why", [
    ("[]", "пустой список — история усечена, а не «прибытий не было»"),
    ('{"date": "2026-07-19"}', "объект вместо списка"),
    ("не json вовсе", "нечитаемый файл"),
    ('["строка вместо снимка"]', "последний элемент не объект"),
])
def test_prior_validation_rejects_every_shape_of_damage(tmp_path, payload, why):
    art = tmp_path / "h.json"
    art.write_text(payload, encoding="utf-8")
    assert la._validate_prior_passset(art)["ok"] is False, why


def test_absent_history_is_legal(tmp_path):
    """Первый прогон вообще: файла нет — это НЕ порча, иначе система не стартует."""
    assert la._validate_prior_passset(tmp_path / "нет.json")["ok"] is True


# ─────────────────── Фаза 2: возврат писателя участвует в решении ───────────────────

class _FakeCG:
    """Минимальный объект вместо DataFrame: `_passset_members` смотрит только .columns."""
    columns: list = []


def test_writer_refusal_blocks_publication(tmp_path, monkeypatch):
    """VG-R4-01b. Отказ ЗАПИСИ состояния = отказ прогона.

    Раньше возврат писателя отбрасывался (`if _write_passset_snapshot(...)`), и OSError при
    записи снимка был неотличим от успеха: вера уезжала поверх непереведённого состояния.
    Вносим настоящий сбой — путь к снимку занят каталогом, запись невозможна."""
    blocked = tmp_path / "занято"
    blocked.mkdir()
    gate_meta = {"gate_applied": True, "status": "applied"}
    st = la._write_passset_snapshot(_FakeCG(), gate_meta, artifact=blocked, write_to=blocked)
    assert st["ok"] is False and st.get("skipped") is not True


def test_cas_conflict_makes_loser_refuse_to_publish(tmp_path, monkeypatch):
    """VG-R4-02. Конкурент, вклинившийся между нашим чтением и записью, НЕ затирается молча.

    Раньше read-modify-write терял чужое обновление, и ОБА процесса выходили с кодом 0.
    Здесь конкурент вносится по-настоящему: подменяем `_atomic_write_json` так, что прямо
    перед нашей записью в файл пишет кто-то другой — то есть воспроизводим гонку, а не
    рассуждаем о ней."""
    art = tmp_path / "h.json"
    art.write_text(json.dumps([_snapshot("2026-07-19", _MEMBERS_EMPTY)]), encoding="utf-8")

    real_read = Path.read_text
    calls = {"n": 0}

    def _racing_read(self, *a, **kw):
        out = real_read(self, *a, **kw)
        # Считаем чтения ИМЕННО артефакта, а не все чтения процесса (правка 2026-08-10).
        # Раньше счётчик рос на КАЖДОМ `Path.read_text`, и это случайно совпадало с
        # числом чтений артефакта. Совпадение сломалось, как только в тот же путь
        # добавилось постороннее чтение файла (`git_facts` берёт HEAD sha из манифеста,
        # когда git недоступен) — конкурент переставал вклиниваться, и тест краснел,
        # ничего не говоря о CAS. Ключ по файлу выражает замысел теста дословно:
        # «между НАШИМ первым чтением артефакта и перечитыванием успел чужой».
        if self != art:
            return out
        calls["n"] += 1
        if calls["n"] == 1:
            art.write_text(json.dumps([_snapshot("2026-07-19", _MEMBERS_EMPTY),
                                       _snapshot("2026-07-26", _MEMBERS_EMPTY)]),
                           encoding="utf-8")
        return out

    monkeypatch.setattr(Path, "read_text", _racing_read)
    st = la._write_passset_snapshot(_FakeCG(), {"gate_applied": True}, artifact=art)
    monkeypatch.undo()
    assert st["conflict"] is True, "проигравший CAS обязан узнать, что проиграл"
    assert st["ok"] is False, "проигравший не пишет"
    # И чужой снимок на месте — потерянного обновления не случилось.
    assert len(json.loads(art.read_text(encoding="utf-8"))) == 2


def test_skipped_when_gate_not_applied(tmp_path):
    """Гейт не применён → писатель не вызывается по существу и помечает это `skipped`,
    чтобы вызывающий не записал отказ дважды под разными причинами."""
    st = la._write_passset_snapshot(_FakeCG(), {"gate_applied": False},
                                    artifact=tmp_path / "h.json")
    assert st["skipped"] is True


# ─────────────────── Фаза 5: конверт прогона закрывается всегда ───────────────────

def test_receipt_opens_started_and_closes_on_crash(tmp_path, monkeypatch):
    """VG-R4-03. Прогон, упавший внутри тела, оставляет ЧЕСТНЫЙ failed, а не прошлый applied.

    Оракул смотрит на файл на диске — то, что увидит датчик в 07:50, а не на возврат функции.
    """
    receipt = tmp_path / "gate_run_receipt.json"
    monkeypatch.setattr(la, "GATE_RUN_RECEIPT", receipt)
    monkeypatch.setattr(la, "_run_body",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой в теле")))
    with pytest.raises(RuntimeError):
        la.run(out_path=tmp_path / "out.xlsx", save_db=True)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    assert rec["status"] == "failed", "упавший прогон не имеет права оставить applied"
    assert rec["published"] is False
    assert rec["run_id"], "у прогона обязана быть идентичность, иначе сверка с верой — по дате"
    assert "сбой в теле" in str(rec.get("error"))


def test_started_receipt_exists_before_body_runs(tmp_path, monkeypatch):
    """Конверт ОТКРЫВАЕТСЯ до тела: убитый по SIGKILL прогон оставляет `started`.

    Без этого убийство процесса оставляло на диске прошлую applied — «всё хорошо» там, где
    прогона не было."""
    receipt = tmp_path / "gate_run_receipt.json"
    monkeypatch.setattr(la, "GATE_RUN_RECEIPT", receipt)
    seen = {}

    def _body(*a, **k):
        seen["rec"] = json.loads(receipt.read_text(encoding="utf-8"))
        return None

    monkeypatch.setattr(la, "_run_body", _body)
    la.run(out_path=tmp_path / "out.xlsx", save_db=True)
    assert seen["rec"]["status"] == "started"
    assert seen["rec"]["phase"] == "started"


def test_dry_run_never_overwrites_production_receipt(tmp_path, monkeypatch):
    """VG-R4-03. Репетиция не стирает улику настоящего отказа.

    Воспроизведено в раунде 4: боевая квитанция `failed` перетиралась `applied` от --no-db.
    """
    receipt = tmp_path / "gate_run_receipt.json"
    receipt.write_text(json.dumps({"status": "failed", "error": "настоящий отказ"}),
                       encoding="utf-8")
    monkeypatch.setattr(la, "GATE_RUN_RECEIPT", receipt)
    monkeypatch.setattr(la, "DRYRUN_DIR", tmp_path / "dryrun")
    monkeypatch.setattr(la, "_run_body", lambda *a, **k: None)
    la.run(out_path=tmp_path / "out.xlsx", save_db=False)
    assert json.loads(receipt.read_text(encoding="utf-8"))["error"] == "настоящий отказ", \
        "репетиция затёрла боевую квитанцию — улика отказа потеряна"
    assert (tmp_path / "dryrun" / "gate_run_receipt.json").exists(), \
        "репетиция обязана оставить СВОЙ след, иначе её нечем проверить"


def test_published_true_only_after_actual_db_write(tmp_path, monkeypatch):
    """`published` в квитанции = «вера ЗАПИСАНА». Тело, не дошедшее до записи, не имеет
    права оставить published=true — иначе датчик подтверждает несуществующую строку."""
    receipt = tmp_path / "gate_run_receipt.json"
    monkeypatch.setattr(la, "GATE_RUN_RECEIPT", receipt)
    monkeypatch.setattr(la, "_run_body", lambda *a, **k: None)   # тело ничего не записало
    la.run(out_path=tmp_path / "out.xlsx", save_db=True)
    assert json.loads(receipt.read_text(encoding="utf-8"))["published"] is False


# ─────────────────── Фаза 4: репетиция не меняет состояние ───────────────────

def test_dry_run_does_not_queue_quarantine(tmp_path):
    """VG-R4-04. Репетиция не создаёт человеческий долг.

    Раньше --no-db ставил пары в карантин и коммитил: пара ждала вердикта, хотя веры за ней
    не стояло."""
    conn = _fixture_conn()
    art = tmp_path / "h.json"
    art.write_text(json.dumps([_snapshot(
        "2026-07-26", _MEMBERS_EMPTY, flicker={"D": {"entered": ["a×b"], "left": []}})]),
        encoding="utf-8")

    la._quarantined_pairs(art, conn=conn, commit=False)
    assert conn.execute("SELECT COUNT(*) FROM passset_quarantine").fetchone()[0] == 0, \
        "репетиция поставила пару в карантин"

    la._quarantined_pairs(art, conn=conn, commit=True)      # негативный контроль
    assert conn.execute("SELECT COUNT(*) FROM passset_quarantine").fetchone()[0] == 1


# ─────────────────── VG-R4-05: порча состояния карантина видима ───────────────────

def test_corrupt_quarantine_rows_are_reported_not_silently_filtered():
    """Строка вне контракта выпадает из выборки читателя — и обязана попасть в датчик.

    Иначе `pending=0` значит равно «вердиктов не ждут» и «состояние нечитаемо»: ровно та
    неотличимость, ради которой затевался весь ремонт подсистемы."""
    # Таблица БЕЗ CHECK'ов — ровно такая лежит на боевой машине: она создана 2026-07-26,
    # до ревью R4, а SQLite не умеет ALTER ADD CONSTRAINT. Строить тест на защищённой
    # фикстуре значило бы проверять мир, в котором находки не бывает.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE passset_quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "pair TEXT, family TEXT, method_epoch TEXT DEFAULT '', "
                 "entered_at TEXT DEFAULT (datetime('now')), status TEXT DEFAULT 'pending', "
                 "resolved_at TEXT, resolution TEXT, resolved_by TEXT, "
                 "UNIQUE(pair, method_epoch))")
    conn.execute("INSERT INTO passset_quarantine (pair, family, method_epoch, status) "
                 "VALUES ('a×b','D','signal_family_v7','pending')")
    conn.commit()
    assert quarantine_db.corrupt_quarantine_rows(conn) == [], "здоровая строка — не находка"

    conn.execute("UPDATE passset_quarantine SET status='PENDING'")   # регистр «сломал» фильтр
    conn.commit()
    assert quarantine_db.pending_quarantine_pairs("signal_family_v7", conn=conn) == set(), \
        "предпосылка теста: испорченная строка невидима читателю"
    bad = quarantine_db.corrupt_quarantine_rows(conn)
    assert len(bad) == 1 and "status" in bad[0]["why"], \
        "невидимая читателю строка обязана быть видима датчику"


def test_schema_guard_reports_absence_of_checks():
    """Живая таблица без CHECK'ов — факт, о котором нельзя молчать: DDL их объявляет, но
    `CREATE TABLE IF NOT EXISTS` не достраивает к уже созданной таблице."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE passset_quarantine (pair TEXT, family TEXT, "
                 "method_epoch TEXT, status TEXT, resolution TEXT, entered_at TEXT, "
                 "id INTEGER PRIMARY KEY)")
    assert quarantine_db.quarantine_schema_guarded(conn) is False
    assert quarantine_db.quarantine_schema_guarded(_fixture_conn()) is True


# ─────────────────── Найдено при доводке: CLI вердиктов был мёртв ───────────────────

def test_cli_epoch_matches_writer_epoch():
    """Гейт ставит пару под `signal_family_vN`, CLI обязан снимать под ТОЙ ЖЕ эпохой.

    До 2026-07-26 CLI звал resolve_quarantine без method_epoch (дефолт ''), rowcount был 0
    ВСЕГДА, и единственный механизм снятия карантина не мог снять ни одной пары — при том
    что список пару показывал. Инструмент выглядел рабочим, будучи мёртвым."""
    conn = _fixture_conn()
    epoch = quarantine_db.method_epoch()
    assert epoch == la._method_epoch(), "две формулы эпохи — это будущий тихий разъезд"
    quarantine_db.queue_quarantine([{"pair": "a×b", "family": "D"}],
                                   method_epoch=epoch, conn=conn)
    assert quarantine_db.resolve_quarantine("a×b", "admitted", "обоснование",
                                            method_epoch=epoch, conn=conn) == 1
    assert quarantine_db.resolve_quarantine("a×b", "admitted", "обоснование",
                                            method_epoch="", conn=conn) == 0, \
        "негативный контроль: пустая эпоха не совпадает — именно так CLI и промахивался"


# ═══════════ Ремонт по внешнему ревью R5 (2026-07-26) ═══════════
# Все находки воспроизведены независимо ДО правки (plans/verify_r5_findings_2026-07-26.py).
# Здесь — ПОСТОЯННЫЕ оракулы: проба одноразова, её никто не зовёт, и она ничего не стережёт.

import pandas as pd                          # noqa: E402


def _corr(pair=("alpha", "beta")):
    return pd.DataFrame([{"metric_a": pair[0], "metric_b": pair[1], "spearman_r": 0.71,
                          "p_value": 0.001, "p_perm": 0.002, "n": 300, "gate_pass": True}])


def _summary_with(decisions):
    yearly = pd.DataFrame([{"year": 2025, "hrv_mean": 40.0}])
    phases_df = pd.DataFrame(columns=["phase", "phase_type", "start", "end", "n_days"])
    return la.build_ai_summary(yearly, phases_df, {}, _corr(), pd.DataFrame(), [],
                               {"gate_applied": True, "status": "applied"},
                               quarantined=decisions)


# ── VG-R5-01: вердикт человека обязан доехать до читателя типом, а не вычитанием ──

def test_rejected_pair_never_reaches_the_reader():
    """Человек назвал связь артефактом. Раньше `rejected` и `admitted` одинаково исчезали из
    множества pending, и отклонённая пара возвращалась в конституции обычной находкой — обход
    последнего человеческого гейта."""
    conn = _fixture_conn()
    epoch = quarantine_db.method_epoch()
    quarantine_db.queue_quarantine([{"pair": "alpha×beta", "family": "D"}], epoch, conn=conn)
    quarantine_db.resolve_quarantine("alpha×beta", "rejected", "артефакт мерцания",
                                     method_epoch=epoch, conn=conn)
    decisions = la._quarantined_pairs(conn=conn, commit=False, entered=[])
    assert decisions == {"alpha×beta": "rejected"}, "читателю едет решение, а не множество"
    s = _summary_with(decisions)
    assert [i for i in s["top_correlations"] if i["a"] == "alpha"] == [], \
        "отклонённая пара доехала до читателя"
    assert s["quarantine"]["rejected_excluded"] == 1, \
        "исключение молчаливо: укороченный список неотличим от «связей не нашлось»"


def test_admitted_pair_stays_a_finding_with_provenance():
    """Позитивный контроль к предыдущему. Без него «всё исчезло» зачлось бы за починку."""
    conn = _fixture_conn()
    epoch = quarantine_db.method_epoch()
    quarantine_db.queue_quarantine([{"pair": "alpha×beta", "family": "D"}], epoch, conn=conn)
    quarantine_db.resolve_quarantine("alpha×beta", "admitted", "держится 4 прогона",
                                     method_epoch=epoch, conn=conn)
    s = _summary_with(la._quarantined_pairs(conn=conn, commit=False, entered=[]))
    item = next(i for i in s["top_correlations"] if i["a"] == "alpha")
    assert item["online_status"] == "admitted"


def test_pending_pair_is_still_not_a_finding():
    """Третье состояние: вердикта нет — и пара подаётся как «состав менялся», не как связь."""
    conn = _fixture_conn()
    epoch = quarantine_db.method_epoch()
    quarantine_db.queue_quarantine([{"pair": "alpha×beta", "family": "D"}], epoch, conn=conn)
    s = _summary_with(la._quarantined_pairs(conn=conn, commit=False, entered=[]))
    item = next(i for i in s["top_correlations"] if i["a"] == "alpha")
    assert item["online_status"] == "pending_adjudication"


# ── VG-R5-02: постановка в карантин — ПРЕДУСЛОВИЕ продвижения pass-set ──

def test_passset_does_not_advance_when_quarantine_ack_fails(tmp_path, monkeypatch):
    """Раньше снимок продвигался первым: при падении БД событие прибытия исчезало на следующей
    календарной дате, и пара получала право войти в веру без чьего-либо вердикта."""
    art = tmp_path / "gate_passset_history.json"
    art.write_text(json.dumps([_snapshot("2026-07-24", _MEMBERS_EMPTY)]), encoding="utf-8")
    monkeypatch.setattr(la, "_passset_members",
                        lambda cg, meta: {"D": ["alpha×beta"], "A": [], "q_lag": []})

    def _dead_ack(snap):
        assert la._entered_from_snap(snap) == [{"pair": "alpha×beta", "family": "D"}], \
            "предпосылка: прибытие видно ДО записи снимка"
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError):
        la._write_passset_snapshot(pd.DataFrame(), {"gate_applied": True, "status": "applied"},
                                   artifact=art, before_commit=_dead_ack)
    hist = json.loads(art.read_text(encoding="utf-8"))
    assert len(hist) == 1 and hist[-1]["date"] == "2026-07-24", \
        "снимок продвинулся, хотя событие прибытия не зафиксировано"


def test_passset_advances_when_ack_succeeds(tmp_path, monkeypatch):
    """Позитивный контроль: без него «никогда не продвигать» прошло бы за починку."""
    art = tmp_path / "gate_passset_history.json"
    art.write_text(json.dumps([_snapshot("2026-07-24", _MEMBERS_EMPTY)]), encoding="utf-8")
    monkeypatch.setattr(la, "_passset_members",
                        lambda cg, meta: {"D": ["alpha×beta"], "A": [], "q_lag": []})
    seen = []
    st = la._write_passset_snapshot(pd.DataFrame(), {"gate_applied": True, "status": "applied"},
                                    artifact=art, before_commit=lambda s: seen.append(s))
    assert st["ok"] and len(json.loads(art.read_text(encoding="utf-8"))) == 2 and seen


# ── VG-R5-04: путь записи проверяется ДО работы ──

def test_out_path_refuses_state_and_databases(tmp_path, monkeypatch):
    """`--no-db --out <файл>` молча заменял этот файл ZIP-контейнером xlsx и выходил с кодом 0.
    Целью мог быть канонический health.db, а `--no-db` создавал ложное чувство безопасности."""
    victim = tmp_path / "health.db"
    conn = sqlite3.connect(victim)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    before = victim.read_bytes()

    with pytest.raises(la.OutPathRefused):
        la._validate_out_path(victim)                       # не .xlsx
    fake = tmp_path / "report.xlsx"                          # БД под именем отчёта
    fake.write_bytes(before)
    with pytest.raises(la.OutPathRefused):
        la._validate_out_path(fake)
    monkeypatch.setattr(la, "_protected_paths", lambda: [victim])
    alias = tmp_path / "alias.xlsx"
    alias.symlink_to(victim)
    with pytest.raises(la.OutPathRefused):
        la._validate_out_path(alias)                         # симлинк на защищаемый путь
    assert victim.read_bytes() == before, "проверка пути сама тронула файл"


def test_out_path_allows_a_normal_report(tmp_path):
    """Позитивный контроль: обычный отчёт проходит. Иначе гард сломал бы штатный прогон."""
    assert la._validate_out_path(tmp_path / "longitudinal_analysis.xlsx").suffix == ".xlsx"


# ── VG-R5-03/07: второго боевого писателя не существует ──

def test_second_production_run_is_refused_and_leaves_first_receipt_alone(monkeypatch, tmp_path):
    """CAS ловил конфликт не всегда: ревьюер поставил двух писателей барьером ПОСЛЕ проверки —
    оба вернули ok=True, одно обновление потерялось. Плюс квитанция singleton: второй прогон
    затирал конверт первого. Обе дыры закрыты тем, что второго боевого прогона больше нет."""
    monkeypatch.setattr(la, "RUN_LOCK", tmp_path / "gate_run.lock")
    receipt = tmp_path / "gate_run_receipt.json"
    la._write_gate_run_receipt({"run_id": "A"}, False, artifact=receipt, run_id="A",
                               phase="started")
    before = receipt.read_bytes()
    first = la._acquire_run_lock(dry_run=False)
    try:
        with pytest.raises(la.RunLockBusy):
            la._acquire_run_lock(dry_run=False)
    finally:
        la._release_run_lock(first)
    assert receipt.read_bytes() == before, "проигравший тронул квитанцию первого"
    second = la._acquire_run_lock(dry_run=False)             # замок освобождён — следующий берёт
    la._release_run_lock(second)


def test_dry_run_does_not_take_the_lock(monkeypatch, tmp_path):
    """Позитивный контроль: репетиция пишет только в песочницу и боевой прогон не блокирует —
    иначе замок стал бы поводом гонять анализ «как-нибудь иначе», то есть мимо гейта."""
    monkeypatch.setattr(la, "RUN_LOCK", tmp_path / "gate_run.lock")
    held = la._acquire_run_lock(dry_run=False)
    try:
        assert la._acquire_run_lock(dry_run=True) is None
    finally:
        la._release_run_lock(held)


# ── VG-R5-08: исход коммита читается, а не выдумывается ──

def test_commit_outcome_is_read_back_not_guessed():
    """`commit` прошёл, подтверждение потерялось — раньше квитанция говорила `failed` при
    ЗАПИСАННОЙ вере, и повторный прогон дописал бы дубль."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE agent_reports (id INTEGER PRIMARY KEY, agent_type TEXT, "
                 "findings TEXT)")
    conn.execute("INSERT INTO agent_reports (agent_type, findings) VALUES "
                 "('longitudinal_analysis', '{\"gate\": {\"run_id\": \"R8\"}}')")
    conn.commit()
    gm = {"run_id": "R8"}
    assert la._resolve_commit_outcome("R8", sqlite3.OperationalError("lost"), gm, conn=conn) \
        == "committed"
    assert gm["published"] is True and "read-back" in gm["commit_note"]

    conn.execute("DELETE FROM agent_reports")
    conn.commit()
    gm2 = {"run_id": "R8"}
    assert la._resolve_commit_outcome("R8", sqlite3.OperationalError("lost"), gm2, conn=conn) \
        == "failed", "строки нет — это честный отказ, а не «вера есть»"
    assert not gm2.get("published")


def test_unreadable_db_gives_indeterminate_not_invented_failure():
    """Третий исход законен: «не знаю» лучше выдуманного `failed`, потому что выдуманный
    провоцирует повторный прогон и дубль веры."""
    class Dead:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    gm = {"run_id": "R9"}
    assert la._resolve_commit_outcome("R9", sqlite3.OperationalError("lost"), gm, conn=Dead()) \
        == "indeterminate"
    assert gm["failure"] == "belief_commit_indeterminate" and "НЕИЗВЕСТЕН" in gm["error"]
