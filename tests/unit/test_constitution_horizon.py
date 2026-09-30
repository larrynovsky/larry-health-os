"""Горизонт конституции (решение владельца 2032-09-25): в конституцию входит только то, у чего
значимое следствие держится не меньше года. Оракулы: (1) проверка вывода recent_mentions,
(2) отбор фаз phase_in_horizon, (3) лаб-ряды в режиме горизонта, (4) чат в промпт не идёт.

Краткосрочные сообщения не должны попадать в долгосрочную конституцию.
Ниже независимо придуманные фрагменты, даты и показатели.
"""
from datetime import date, timedelta

import pytest

import generate_constitutions as gc

TODAY = date(2032, 9, 25)
H = 365


# ── (1) оракул вывода ────────────────────────────────────────────────────────

@pytest.mark.parametrize("frag", [
    "HRV на неделе 14–20 сентября: точка 29 мс 20.09",
    "короткая ночь 2032-09-20",
    "протокол с 01.08.2032",
    "за последние 7 дней глубокий сон 37 мин",
    "на прошлой неделе HRV 47–59 мс",
    "контроль анализа X — октябрь 2032",
    "держать протокол до 31.12.2032",
    "Ближайший месяц — точка калибровки",
])
def test_recent_fragment_is_caught(frag):
    assert gc.recent_mentions(frag, TODAY, H), frag


@pytest.mark.parametrize("frag", [
    "эффективность сна 87.2% против 69.4%",
    "общее время сна 6.8 ч, HRV 52.3 мс, REM 1.17 ч",
    "операция X в 2025 году оставила след на годы",
    "эпизод X в марте 2027 года",
    "год к году 4.8 → 5.2 → 5.6 (2025→2028)",
    "за последние 5 лет тренд вниз",
    "**Обновлено:** 2032-09-25",
    "r=0.641 (p=0.003)",
])
def test_long_horizon_fragment_is_not_caught(frag):
    assert gc.recent_mentions(frag, TODAY, H) == [], frag


def test_invented_short_horizon_shape_is_caught():
    """Вымышленный текст с тремя оперативными привязками даёт три находки+."""
    text = ("**Обновлено:** 2032-09-25\n"
            "Одиночные ночи ≤3.5 ч (как 20.09.2032) при узком буфере.\n"
            "Deep sleep 37 мин/ночь за последние 7 дней.\n"
            "- **Протокол:** держать минимум до 31.12.2032.\n")
    assert len(gc.recent_mentions(text, TODAY, H)) >= 3


# ── (2) отбор фаз ────────────────────────────────────────────────────────────

def _ph(start, end, typ="treatment"):
    return {"phase": "x", "type": typ, "start": start, "end": end, "n": 30}


def test_phase_selection():
    assert not gc.phase_in_horizon(_ph("2032-03-02", "2032-03-09", "travel"), TODAY, H)   # поездка
    assert gc.phase_in_horizon(_ph("2027-02-01", "2027-03-01"), TODAY, H)                 # давняя причина
    assert not gc.phase_in_horizon(_ph("2032-01-10", "2032-09-19", "period"), TODAY, H)  # окно < года
    assert gc.phase_in_horizon(_ph("2026-03-01", "2032-09-19", "baseline"), TODAY, H)     # долгая идущая


# ── (3) лаб-ряды в режиме горизонта ──────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    health_db._ensure_lab_table()
    return health_db


def _ins(db, rows):
    with db.get_conn() as c:
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)", rows)
        c.commit()


def _d(n):
    return (date.today() - timedelta(days=n)).isoformat()


def test_lab_horizon_keeps_long_series_drops_fresh_and_short(db):
    import labs_db
    _ins(db, [(_d(1500), "Ferritin", 45.0, "ng/ml"), (_d(800), "Ferritin", 30.0, "ng/ml"),
              (_d(30), "Ferritin", 21.0, "ng/ml"),                                  # ряд 4 года
              (_d(200), "TSH", 1.1, "mIU/L"), (_d(100), "TSH", 1.2, "mIU/L"),
              (_d(20), "TSH", 1.3, "mIU/L"),                                         # 3 точки, < года
              (_d(10), "NT_proBNP", 28.0, "pg/ml")])                                 # одна свежая
    ctx = labs_db.build_lab_history_context(horizon_days=365)
    assert "Ferritin" in ctx
    first_year = (date.today() - timedelta(days=1500)).year
    assert f"{first_year}: 45.0" in ctx, "начало многолетнего ряда обязано быть видно (единица — год)"
    assert "TSH" not in ctx and "NT_proBNP" not in ctx
    assert "ТЕКУЩЕЕ" not in ctx
    # прежний режим для остальных читателей не тронут
    assert "ТЕКУЩЕЕ" in labs_db.build_lab_history_context()


def test_constitution_does_not_call_single_draw_or_specialized_blocks():
    """Разовые заборы и спец-панели — не устройство (нет ряда); их дом — GP и консилиум."""
    import inspect
    src = inspect.getsource(gc._get_metric_summary)
    assert "build_unrepeated_draw_context" not in src
    assert "build_specialized_context" not in src
    assert "horizon_days=" in src


# ── (4) чат в промпт не идёт ─────────────────────────────────────────────────

def test_chat_block_not_in_constitution_prompt(monkeypatch):
    import patient_context

    def _boom():
        raise AssertionError("reasoning_block не должен звучать в конституции")
    monkeypatch.setattr(patient_context, "reasoning_block", _boom)
    monkeypatch.setattr(gc, "_get_snp_data", lambda cfg: {"bad": [{"rsid": "rs1", "gene": "PER3",
                        "significance": "risk factor", "clinical_summary": "s"}], "good": [], "unknown": []})
    monkeypatch.setattr(gc, "_get_metric_summary", lambda keys: "  тренд")
    monkeypatch.setattr(gc, "_get_medical_history", lambda: "  2025 | операция X (surgery)")
    monkeypatch.setattr(gc, "_read_previous", lambda d: "")
    monkeypatch.setattr(gc, "horizon_days", lambda: 365)
    real, seen = gc._build_prompt, {}

    def _spy(*a, **k):
        seen["p"] = real(*a, **k)
        return seen["p"]
    monkeypatch.setattr(gc, "_build_prompt", _spy)
    assert gc._generate_one("sleep", dry_run=True) == ""
    assert "Заметки из диалогов" not in seen["p"]
    assert "ГОРИЗОНТ (365 дней)" in seen["p"]


def test_generation_refuses_to_save_after_second_violation(monkeypatch):
    """Оракул вывода стоит в цепочке: два нарушения подряд → None (сбой, не пропуск)."""
    monkeypatch.setattr(gc, "_get_snp_data", lambda cfg: {"bad": [{"rsid": "rs1", "gene": "PER3",
                        "significance": "risk factor", "clinical_summary": "s"}], "good": [], "unknown": []})
    monkeypatch.setattr(gc, "_get_metric_summary", lambda keys: "  тренд")
    monkeypatch.setattr(gc, "_get_medical_history", lambda: "  2025 | операция X (surgery)")
    monkeypatch.setattr(gc, "_read_previous", lambda d: "")
    monkeypatch.setattr(gc, "horizon_days", lambda: 365)
    monkeypatch.setattr(gc, "get_today", lambda: TODAY)
    calls = []
    monkeypatch.setattr(gc, "_call_claude", lambda p, max_tokens=0: calls.append(p) or
                        "# Конституция: Сон\nночь 20.09 была короткой")
    assert gc._generate_one("sleep") is None
    assert len(calls) == 2 and "НАРУШИЛ ГОРИЗОНТ" in calls[1]
