"""Нить triage-cost-leak (23.10.2026) — отчёт с GitHub, v0.2.4 (Docker):
триаж каждое утро гонял полный консилиум на Opus, пока gp_weekly не сохранялся.

Четыре механизма, каждый со своим красным:
  1. триаж ничего не запускает — tests/integration/test_uc_d_04_triage.py;
  2. несохранённый GP-отчёт (отказ сторожа, ошибка записи) — событие в журнале сбоев;
  3. датчик «GP ≤ 8 дней» у установки моложе порога без единого отчёта — warn, не fail;
     у взрослой установки без отчёта в окне — по-прежнему fail (в обе стороны, §17);
  4. «usage limits» от Anthropic — проблема счёта, а не документа.
Журнал всех вызовов модели — tests/unit/test_llm_task_modes.py.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.unit


# ── 2. отказ и несохранение — события ────────────────────────────────────────

def _faults(monkeypatch):
    import notify
    got = []
    monkeypatch.setattr(notify, "fault", lambda tech, person_key="x", **kw: got.append((tech, person_key)))
    return got


def _weekly_with_rejected_guard(monkeypatch):
    """generate_weekly_report без базы и без модели: контекст — строка, модель — фейк,
    сторож — отказ. Что проверяем: путь после отказа, не содержимое отчёта."""
    import gp_agent
    monkeypatch.setattr(gp_agent, "_build_gp_context", lambda *a, **k: "КОНТЕКСТ")
    monkeypatch.setattr(gp_agent, "_build_gp_system_prompt", lambda: "SYS")
    resp = NS(content=[NS(type="text", text="psa не сдавался")])
    client = NS(messages=NS(create=lambda **kw: resp))
    monkeypatch.setattr(gp_agent, "_get_client", lambda: client)
    monkeypatch.setattr(gp_agent.llm_client, "answer_text", lambda r: "psa не сдавался")

    def _reject(*a, **k):
        raise gp_agent._AbsentClaimRejected("отчёт weekly не отправлен: `PSA` — последняя строка 2024-01-01")
    monkeypatch.setattr(gp_agent, "_guard_absent_claims", _reject)
    saved = []
    monkeypatch.setattr(gp_agent, "_save_gp_report", lambda *a, **k: saved.append(a))
    return gp_agent, saved


def test_rejected_weekly_is_a_fault_event_and_is_not_saved(monkeypatch):
    """⛔ раньше жил только в ERROR-строке лога процесса; у Docker-установки её никто не
    читал, датчик краснел неделю, триаж платил. Мутация «убрать _rejection_is_an_event
    из weekly» — красная."""
    got = _faults(monkeypatch)
    gp_agent, saved = _weekly_with_rejected_guard(monkeypatch)
    out = gp_agent.generate_weekly_report(date(2026, 10, 19), run_mdt=False)
    assert out.startswith("⛔") and "`PSA`" in out
    assert saved == [], "отклонённый отчёт не сохраняется (решение владельца 13.09)"
    assert len(got) == 1 and got[0][1] is None
    assert got[0][0].startswith("gp_agent.weekly:") and "rejected" in got[0][0] and "`PSA`" in got[0][0]


def test_rejection_event_never_hides_the_rejection(monkeypatch):
    """Сломанный notify — отказ всё равно возвращается получателю."""
    import gp_agent, notify

    def _boom(*a, **k):
        raise RuntimeError("журнал недоступен")
    monkeypatch.setattr(notify, "fault", _boom)
    gp_agent._rejection_is_an_event("weekly", gp_agent._AbsentClaimRejected("x"))   # не бросает


def test_unsaved_gp_report_is_a_fault_event(monkeypatch):
    """Ошибка записи в agent_reports — событие с именем отчёта и датой, не warning."""
    got = _faults(monkeypatch)
    import gp_agent, health_db

    def _boom(**kw):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(health_db, "save_agent_report", _boom)
    gp_agent._save_gp_report("weekly", date(2026, 10, 19), "текст", period_days=7)
    assert len(got) == 1 and got[0][1] is None
    tech = got[0][0]
    assert tech.startswith("gp_agent._save_gp_report:") and "weekly" in tech \
        and "2026-10-19" in tech and "database is locked" in tech


# ── 3. грейс новорождённой установки, в обе стороны ──────────────────────────

def _integrity(monkeypatch, *, today: date, born: str | None, in_window: list, ever: list):
    import integrity_tests as I
    import config_db
    monkeypatch.setattr(I, "today", today)
    monkeypatch.setattr(config_db, "get_config", lambda key, default=None, conn=None:
                        born if key == "install.first_run" else default)

    def _reports(date_from, agent_type=None):
        assert agent_type == "gp"
        return list(in_window) if date_from > "1970-01-01" else list(ever)
    monkeypatch.setattr(I.db, "get_reports_with_findings", _reports)
    warned = []
    monkeypatch.setattr(I, "warn", lambda label, detail="": warned.append(detail))
    return I, warned


def test_newborn_install_without_any_report_warns_not_fails(monkeypatch):
    I, warned = _integrity(monkeypatch, today=date(2026, 10, 21), born="2026-10-18",
                           in_window=[], ever=[])
    assert I.check_gp_reports() is None
    assert warned and "младше" in warned[0] and "3 дн." in warned[0]


def test_install_older_than_threshold_without_any_report_fails(monkeypatch):
    """Граница грейса: 9 дней без единого отчёта — уже поломка воскресного пути."""
    I, warned = _integrity(monkeypatch, today=date(2026, 10, 27), born="2026-10-18",
                           in_window=[], ever=[])
    with pytest.raises(AssertionError, match="нет ни одного GP-отчёта"):
        I.check_gp_reports()
    assert warned == []


def test_adult_install_that_lost_its_report_still_fails(monkeypatch):
    """Обратная сторона (§17): у взрослой установки отчёты БЫЛИ, в окне нет — fail, грейс
    не распространяется. Мутация «грейс по in_window вместо ever» — красная здесь."""
    old = [{"date": "2026-06-06", "findings": "x" * 600}]
    I, warned = _integrity(monkeypatch, today=date(2026, 10, 21), born="2026-10-18",
                           in_window=[], ever=old)
    with pytest.raises(AssertionError, match="нет ни одного GP-отчёта"):
        I.check_gp_reports()
    assert warned == []


def test_install_without_birth_key_keeps_old_behaviour(monkeypatch):
    """Ключа нет (база до сида) — датчик судит как раньше: красный."""
    I, warned = _integrity(monkeypatch, today=date(2026, 10, 21), born=None,
                           in_window=[], ever=[])
    with pytest.raises(AssertionError):
        I.check_gp_reports()


def test_fresh_report_passes_regardless_of_birth(monkeypatch):
    fresh = [{"date": "2026-10-19", "findings": "x" * 600}]
    I, warned = _integrity(monkeypatch, today=date(2026, 10, 21), born="2026-10-18",
                           in_window=fresh, ever=fresh)
    assert I.check_gp_reports()["date"] == "2026-10-19" and warned == []


def test_birth_key_is_seeded_once_by_init_db(tmp_path, monkeypatch):
    """Сид install.first_run: пишется при init_db на пустой базе и не перезаписывается."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db, config_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    born = config_db.get_config("install.first_run")
    assert born and len(str(born)) == 10, born
    with health_db.get_conn() as c:
        c.execute("UPDATE system_config SET value_text='2000-01-01' WHERE key='install.first_run'")
    health_db.init_db()
    assert config_db.get_config("install.first_run") == "2000-01-01"


# ── 4. лимит, выставленный владельцем ключа, — проблема счёта ─────────────────

def test_usage_limits_is_an_account_problem():
    import httpx, anthropic, llm_client
    req = httpx.Request("POST", "https://api.example/v1")
    e = anthropic.BadRequestError("You have reached your specified API usage limits",
                                  response=httpx.Response(400, request=req), body=None)
    assert llm_client.is_account_problem(e)
    assert not llm_client.is_transient(e)
