"""Сторож рельсы доставки warn (инцидент 2026-07-13…26: 13 дней слепоты).

Класс: механизм-ПАССАЖИР. `run_triage` жил внутри `morning_report.__main__`; носителя вывели
из расписания — доставка уехала с ним молча. integrity исправно писал warnings, triage.log
показывал свежие записи (следы юнит-теста), и ни один датчик не долетал до владельца.

Сторож FAIL-уровня СОЗНАТЕЛЬНО: сообщение о смерти рельсы не может ехать по этой же рельсе.

РЕДАКЦИЯ 2026-09-07. Судимый артефакт теперь ОДИН — квитанция прогона. До этого дня датчик
сравнивал маркер `triage_done_*.flag` (пишется каждый прогон) с квитанцией (писалась только
когда было что доставлять): в тихий день они расходились, и наутро владельцу ехал ложный
warn «квитанция отстала от маркера» — 2 тихих дня из 2 с момента ввода квитанции 29.07.
Хуже шума: стоявший здесь `assert stale_receipt or via not in (...)` в этом состоянии НЕ
ПРОВЕРЯЛ канал вовсе, то есть сторож последнего метра был выключен в день после каждого
тихого дня. Оба факта воспроизведены исполнением до правки, см. `test_causal_*` ниже.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _mk(tmp_path: Path, warnings: int, run_days_ago: int | None = 0, *,
        via="telegram", via_digest=None, questions=None, digest=0,
        sent=True, proven_days_ago="same", marker_days_ago=None, legacy=False):
    """Раскладывает logs/ так, как их оставил бы прогон триажа.

    `run_days_ago=None` — квитанции нет вовсе. `legacy=True` — форма до 2026-09-07
    ({date, via, questions}), она обязана читаться. `marker_days_ago` кладёт
    `triage_done_*.flag`: датчик его больше не судит, и тесты это проверяют.
    """
    import integrity_tests as I
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    logs.joinpath("integrity_latest.json").write_text(
        json.dumps({"warnings": [[f"w{i}", "detail"] for i in range(warnings)], "failures": []}),
        encoding="utf-8")
    if marker_days_ago is not None:
        logs.joinpath(f"triage_done_{I.today - timedelta(days=marker_days_ago)}.flag").touch()
    if run_days_ago is None:
        return tmp_path
    day = I.today - timedelta(days=run_days_ago)
    q = warnings if questions is None else questions
    if legacy:
        rec = {"date": str(day), "via": via, "questions": q}
    else:
        if proven_days_ago is None:
            proven = None
        elif proven_days_ago == "same":
            proven = day
        else:
            proven = I.today - timedelta(days=proven_days_ago)
        rec = {"date": str(day), "sent": sent, "questions": q, "via": via,
               "digest": digest, "via_digest": via_digest,
               "last_proven": str(proven) if proven else None}
    logs.joinpath("triage_delivery_latest.json").write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


MORNING = (2026, 9, 23, 7, 50)   # среда, момент утреннего integrity (07:50 по плисту)


def _run(tmp_path, monkeypatch, **kw):
    """С 23.09 рельса судится по расписанию триажа (плист 08:00 в tests/conftest.py), канал —
    по последнему понедельничному дайджесту. Оба зависят от дня и часа, поэтому часы
    закреплены на утре среды: последний запуск триажа — вторник 08:00, дайджест — понедельник."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    from datetime import date as _d, datetime as _dt
    import integrity_tests as I
    monkeypatch.setattr(I, "today", _d(*MORNING[:3]))
    monkeypatch.setattr(I, "get_now", lambda *a, **k: _dt(*MORNING))
    cap: list[tuple[str, str]] = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    _mk(tmp_path, **kw)
    return I, cap


# ── 1. Жива ли рельса ────────────────────────────────────────────────────────

def test_rail_dead_is_loud(tmp_path, monkeypatch):
    """⭐ Ровно инцидент: warn есть, прогонов нет 13 дней → FAIL (не warn, не тишина)."""
    I, _ = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=13)
    with pytest.raises(AssertionError, match="РЕЛЬСА ДОСТАВКИ"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_rail_never_ran_is_loud(tmp_path, monkeypatch):
    """Квитанции нет вовсе (чистая машина с накопленными warn) — FAIL, не «нечего судить»."""
    I, _ = _run(tmp_path, monkeypatch, warnings=3, run_days_ago=None)
    with pytest.raises(AssertionError, match="НИКОГДА"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_future_stamp_is_not_freshness(tmp_path, monkeypatch):
    """Штамп из будущего — поломка часов или подделка, а не свежесть (урок ревью 24.07:
    тот же age<0 чинили в check_mc_gap и check_passset_flicker)."""
    I, _ = _run(tmp_path, monkeypatch, warnings=3, run_days_ago=-2)
    with pytest.raises(AssertionError, match="РЕЛЬСА ДОСТАВКИ"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_marker_is_no_longer_judged(tmp_path, monkeypatch):
    """Маркер перестал быть оракулом: свежая квитанция БЕЗ единого `triage_done_*.flag`
    — это норма. Мутация «вернуть сравнение с маркером» уронит этот тест."""
    I, cap = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=0, marker_days_ago=None)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert r["age_days"] == 0 and cap == []


# ── 2. Доказана ли доставка ──────────────────────────────────────────────────

def test_fresh_delivery_is_silent(tmp_path, monkeypatch):
    """Доставка была вчера → молчим (иначе сторож кричал бы каждый день = alarm fatigue)."""
    I, cap = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=1)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert r["age_days"] == 1 and cap == []


def test_delivery_that_reached_nobody_is_loud(tmp_path, monkeypatch):
    """⭐ Класс, невидимый до 2026-07-29: триаж отработал, лог написал «sent» — а оба
    канала вернули 'none'. Человек не получил НИЧЕГО. FAIL, не warn: сообщение об этом
    не может ехать по той же рельсе, что не доехала."""
    I, _ = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=1, via="none",
                proven_days_ago=1)
    with pytest.raises(AssertionError, match="ДОСТАВКА НЕ ДОКАЗАНА"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_unnamed_channel_is_loud(tmp_path, monkeypatch):
    """via=None при непустой доставке — квитанцию писал не триаж (чужой процесс или тест:
    до 2026-08-07 её путь был модульной константой). Не «доставки не было», а «доставка
    НЕ ДОКАЗАНА» (§14: liveness ≠ корректность)."""
    I, _ = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=1, via=None, proven_days_ago=1)
    with pytest.raises(AssertionError, match="ДОСТАВКА НЕ ДОКАЗАНА"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_fallback_channel_counts_as_delivered(tmp_path, monkeypatch):
    """Резервный канал — законная доставка, а не полуотказ: anti-SPOF ради того и строился."""
    I, cap = _run(tmp_path, monkeypatch, warnings=9, run_days_ago=1, via="fallback")
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert r["delivered_via"] == "fallback" and cap == []


# ── 3. Тихий день: главный дефект 2026-09-07 ─────────────────────────────────

def test_causal_state_of_2026_09_07_is_silent(tmp_path, monkeypatch):
    """⭐ ПРИЧИННЫЙ ОРАКУЛ на РЕАЛЬНОМ состоянии утра 2026-09-07 (Studio).

    06.09 был тихим днём (`DONE: needs_user=[]`, всё уехало в понедельничный дайджест):
    маркер встал на 06.09, квитанция осталась на 05.09 — в форме ДО правки. На этих
    данных старый код давал ровно один warn «квитанция доставки триажа отстала от
    маркера: маркер 2026-09-06, квитанция 2026-09-05», который и уехал владельцу в
    Telegram. Воспроизведено исполнением до единой правки: 1 warn, после — 0.

    Замер по logs/triage.log: тихих дней с момента ввода квитанции 29.07 было два
    (04.09 и 06.09), и оба дали ложный варн наутро — совпадение 2 из 2."""
    I, cap = _run(tmp_path, monkeypatch, warnings=7, run_days_ago=1, legacy=True,
                  via="telegram", questions=2, marker_days_ago=0)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert cap == [], f"ложный варн вернулся: {cap}"
    assert r["age_days"] == 1
    # Сдвиг 23.09 (решение владельца «общее правило»): исходное состояние 07.09 — квитанция
    # на ДВА дня позади — после 07.09 невозможно (квитанцию пишет каждый прогон), и теперь
    # оно значит ровно одно: вчерашний плановый запуск триажа не отметился. Это FAIL.
    I, _ = _run(tmp_path, monkeypatch, warnings=7, run_days_ago=2, legacy=True,
                via="telegram", questions=2, marker_days_ago=1)
    with pytest.raises(AssertionError, match="РЕЛЬСА ДОСТАВКИ"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_quiet_day_leaves_receipt_and_is_silent(tmp_path, monkeypatch):
    """Как тихий день выглядит ПОСЛЕ правки: квитанция сегодняшняя, доставлять было
    нечего, канал не назван — и это норма, а не «канал не отчитался». Старый код на
    этом состоянии ронял ЛОЖНЫЙ FAIL «ДОСТАВКА НЕ ДОКАЗАНА» (via=None при свежей
    квитанции), потому что не различал «нечего слать» и «не доставлено»."""
    I, cap = _run(tmp_path, monkeypatch, warnings=7, run_days_ago=0,
                  via=None, questions=0, digest=0, proven_days_ago=2, marker_days_ago=0)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert cap == [], f"ложный варн вернулся: {cap}"
    assert r["due"] == 0


def test_causal_dead_channel_after_quiet_day_is_loud(tmp_path, monkeypatch):
    """⭐⭐ ПОЗИТИВНЫЙ КОНТРОЛЬ на обезвреженный сторож — тяжелее шума выше.

    В датчике стояло `assert stale_receipt or via not in (None, '', 'none')`. Пока
    квитанция «отставала» от маркера, левый операнд был истинен и КАНАЛ НЕ ПРОВЕРЯЛСЯ.
    А отставала она после каждого тихого дня. Воспроизведено 2026-09-07: то же
    состояние с via='none' FAIL не дало — датчик вернул delivered_via='none' и
    промолчал. То есть в день после каждого тихого дня оба канала могли лечь
    незамеченными.

    Здесь: квитанция вчерашняя, доставлять было что, каналы легли → обязан быть FAIL."""
    I, _ = _run(tmp_path, monkeypatch, warnings=7, run_days_ago=1, via="none",
                questions=2, proven_days_ago=3, marker_days_ago=0)
    with pytest.raises(AssertionError, match="ДОСТАВКА НЕ ДОКАЗАНА"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_digest_only_monday_is_silent(tmp_path, monkeypatch):
    """Понедельник, уехал только дайджест: вопросов нет, канал дайджеста назван.
    До 2026-09-07 `via_d` не записывался никуда, и такой день оставлял квитанцию позади."""
    I, cap = _run(tmp_path, monkeypatch, warnings=5, run_days_ago=0, via=None,
                  questions=0, digest=5, via_digest="telegram")
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert cap == [] and r["digest_via"] == "telegram"


def test_digest_that_reached_nobody_is_loud(tmp_path, monkeypatch):
    """Дайджест — тоже доставка человеку. Легли каналы на нём — тот же FAIL."""
    I, _ = _run(tmp_path, monkeypatch, warnings=5, run_days_ago=0, via=None,
                questions=0, digest=5, via_digest="none", proven_days_ago=2)
    with pytest.raises(AssertionError, match="ДОСТАВКА НЕ ДОКАЗАНА"):
        I.check_triage_delivery_liveness(base=tmp_path)


# ── 4. Бюджет молчания канала (решение владельца 2026-09-07: N=9, FAIL) ──────

def test_channel_unproven_too_long_is_loud(tmp_path, monkeypatch):
    """Рельса жива, тихих дней подряд много — и канал не подтверждён дольше порога.
    Дыра, которую это закрывает: пункт «доставка доказана» молчит в тихий день (нечего
    доказывать), значит канал мог умереть недели назад и обнаружиться на первом же
    настоящем варне. FAIL, а не warn: сообщение «канал, возможно, мёртв» не может
    ехать по этому каналу."""
    I, _ = _run(tmp_path, monkeypatch, warnings=4, run_days_ago=0, via=None,
                questions=0, proven_days_ago=10)
    with pytest.raises(AssertionError, match="КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_channel_proven_by_last_monday_digest_is_silent(tmp_path, monkeypatch):
    """Среда: последняя доказанная доставка — понедельничный дайджест (2 дня назад). Это
    граница «общего правила» 23.09: гарантированная доставка — раз в неделю, в день
    дайджеста, и после неё пропуска ещё не было."""
    I, cap = _run(tmp_path, monkeypatch, warnings=4, run_days_ago=0, via=None,
                  questions=0, proven_days_ago=2)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert cap == [] and r["proven_age_days"] == 2


def test_channel_unproven_since_before_last_digest_is_loud(tmp_path, monkeypatch):
    """Негативный контроль к тесту выше: доставка доказана в воскресенье, а понедельничный
    дайджест — нет. Это первый пропуск гарантированной доставки → FAIL. До 23.09 это
    молчало ещё шесть дней (порог 9)."""
    I, _ = _run(tmp_path, monkeypatch, warnings=4, run_days_ago=0, via=None,
                questions=0, proven_days_ago=3)
    with pytest.raises(AssertionError, match="КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_channel_never_proven_is_loud(tmp_path, monkeypatch):
    """`last_proven=None` при живой рельсе — канал не подтверждался НИКОГДА."""
    I, _ = _run(tmp_path, monkeypatch, warnings=4, run_days_ago=0, via=None,
                questions=0, proven_days_ago=None)
    with pytest.raises(AssertionError, match="КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН"):
        I.check_triage_delivery_liveness(base=tmp_path)


# ── 5. Совместимость и границы ───────────────────────────────────────────────

def test_legacy_receipt_still_proves_channel(tmp_path, monkeypatch):
    """Квитанция в форме до 2026-09-07 ({date, via, questions}, без last_proven) лежит
    на Studio в момент деплоя. Без обратной совместимости первое же утро дало бы ложный
    FAIL «канал не подтверждён НИКОГДА» — деплой сам себя объявил бы поломкой."""
    I, cap = _run(tmp_path, monkeypatch, warnings=6, run_days_ago=0, legacy=True)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert cap == [] and r["last_proven"] == r["run_day"]


def test_corrupt_receipt_is_loud_not_silent(tmp_path, monkeypatch):
    """Битая квитанция = «датчик ослеп», а не «всё хорошо». Молчание здесь было бы тем
    самым классом «исключение → тишина», за который в проекте стоит AST-сторож."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    cap: list[tuple[str, str]] = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    _mk(tmp_path, warnings=5, run_days_ago=0)
    (tmp_path / "logs" / "triage_delivery_latest.json").write_text("{битый", encoding="utf-8")
    with pytest.raises(AssertionError, match="РЕЛЬСА ДОСТАВКИ"):
        I.check_triage_delivery_liveness(base=tmp_path)
    assert any("не читается" in n for n, _ in cap), cap


def test_no_warnings_nothing_to_deliver(tmp_path, monkeypatch):
    """Нет warn — канал всё равно обязан подтверждать доставку раз в неделю."""
    I, cap = _run(tmp_path, monkeypatch, warnings=0, run_days_ago=0)
    r = I.check_triage_delivery_liveness(base=tmp_path)
    assert r["warnings"] == 0 and cap == []


@pytest.mark.parametrize("via, proven", [("telegram", True), ("fallback", True),
                                       ("none", False), (None, False)])
def test_quiet_week_requires_delivered_owner_summary(tmp_path, monkeypatch, via, proven):
    """C-73: исполняем писателя квитанции; меняется только ответ транспорта."""
    from datetime import datetime
    import owner_weekly as weekly
    import notify
    import i18n
    I, _ = _run(tmp_path, monkeypatch, warnings=0, run_days_ago=0,
                via=None, questions=0, proven_days_ago=None)
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "ru")
    monkeypatch.setattr(weekly.parked_decisions, "list_open", lambda *a: [])
    monkeypatch.setattr(notify, "notify_operator", lambda text: via)
    rec = tmp_path / "logs" / "owner_weekly_last_run.json"
    monkeypatch.setattr(weekly, "receipt_path", lambda base=None: rec)
    weekly.run(datetime(2026, 9, 21, 9, 10, tzinfo=weekly.owner_nag.HOME_TZ))
    if proven:
        assert I.check_triage_delivery_liveness(base=tmp_path)["last_proven"] == "2026-09-21"
    else:
        with pytest.raises(AssertionError, match="КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН"):
            I.check_triage_delivery_liveness(base=tmp_path)


def test_weekly_receipt_does_not_hide_failed_personal_delivery(tmp_path, monkeypatch):
    I, _ = _run(tmp_path, monkeypatch, warnings=1, questions=1, via="none", proven_days_ago=None)
    (tmp_path / "logs" / "owner_weekly_last_run.json").write_text(json.dumps(
        {"date": "2026-09-21", "sent": True, "via": "telegram"}))
    with pytest.raises(AssertionError, match="ДОСТАВКА НЕ ДОКАЗАНА"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_weekly_receipt_without_transport_is_not_proof(tmp_path, monkeypatch):
    I, _ = _run(tmp_path, monkeypatch, warnings=0, questions=0, via=None, proven_days_ago=None)
    (tmp_path / "logs" / "owner_weekly_last_run.json").write_text(json.dumps(
        {"date": "2026-09-21", "sent": True, "via": "none", "last_proven": "2026-09-21"}))
    with pytest.raises(AssertionError, match="КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН"):
        I.check_triage_delivery_liveness(base=tmp_path)


def test_missing_json_is_silent(tmp_path, monkeypatch):
    """Нет integrity_latest.json (первый запуск) — судить не о чем."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    (tmp_path / "logs").mkdir(exist_ok=True)
    assert I.check_triage_delivery_liveness(base=tmp_path) is None


def test_triage_main_uses_guarded():
    """__main__ обязан звать guarded-версию: голый run_triage делает падение канала тихим.
    Именно эта деталь превратила отставку morning_report в 13 дней слепоты."""
    src = (ROOT / "triage_agent.py").read_text(encoding="utf-8")
    tail = src.split('if __name__ == "__main__":')[-1]
    assert "run_triage_guarded" in tail, "__main__ зовёт незащищённый run_triage"


def test_receipt_written_before_marker():
    """Порядок в run_triage: квитанция ПЕРЕД `done_marker.touch()`. Обратный порядок
    вернул бы маркер без квитанции — ровно то расхождение, которое чинили 2026-09-07."""
    src = (ROOT / "triage_agent.py").read_text(encoding="utf-8")
    body = src.split("def run_triage(")[-1]
    assert body.index("_write_run_receipt(") < body.index("done_marker.touch()"), \
        "маркер ставится раньше квитанции — расхождение может вернуться"
