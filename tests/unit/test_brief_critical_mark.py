"""
tests/unit/test_brief_critical_mark.py — оракул метки недостоверности на брифе.

Повод (замер 11.08.2026): владельцу ушло «🚨 утренний отчёт заблокирован
(1 критических)», а бриф вышел в 08:30 как обычно. Блокировки не существовало:
ни одна из 134 регистраций не несла critical=True, читателя вердикта у брифа
не было вовсе, а sys.exit(2) в check() — сработай он — оборвал бы прогон
посреди и лишил бы триаж артефакта.

Решение владельца 12.08: не блокировать, а метить. Задержать бриф значит отнять
сутки наблюдения — цену этого мы измерили на инциденте 10.08. Метка сохраняет
наблюдение и стоит ровно там, где принимается решение.

Что краснеет при поломке:
  · вердикт без границы свежести (вчерашнее «чисто» принято за сегодняшнее);
  · бриф, вышедший БЕЗ метки поверх свежего критического вердикта;
  · метка, потерявшаяся в файле отчёта (его читают позже Telegram-сообщения);
  · метка под Markdown — `_` в именах датчиков валит доставку (инцидент 03.07);
  · detail датчика, утёкший в Telegram (§19: значения анализов не покидают дом).
"""
from __future__ import annotations

import asyncio
import json
import types
from datetime import date, timedelta

import pytest

import health_db
import jobs.scheduled as sched
import triage_agent as tri

TODAY = date(2026, 8, 12)


@pytest.fixture
def artefact(tmp_path, monkeypatch):
    """Уводит артефакт монитора и «сегодня» в tmp — тест не должен зависеть от
    боевого logs/integrity_latest.json (§20: зелёный обязан быть вызван тестом,
    а не тем, что на Studio сегодня спокойно)."""
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(tri, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tri, "get_today", lambda: TODAY)

    def write(payload):
        # C-19: артефакт несёт провенанс базы (чьи данные судил прогон); фикстура
        # ставит СВОЮ базу по умолчанию — тесты про чужую/отсутствующую пишут поле
        # сами (dict с db_path) или сырую строку (это видно в самих тестах).
        if isinstance(payload, dict) and "db_path" not in payload:
            payload = {"db_path": str(health_db.DB_PATH), **payload}
        (tmp_path / "logs" / "integrity_latest.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload))
    return write


def test_foreign_db_verdict_is_refused(artefact):
    """⭐ C-19: вердикт о ЧУЖОЙ базе не годится этому тенанту — партнёрский бриф
    судил свою достоверность по вердикту о данных владельца (замер 2026-08-12:
    integrity-джоб один, владельческий, а брифов-читателей два)."""
    artefact({"date": str(TODAY), "critical": [["датчик", "деталь"]],
              "db_path": "/tenants/other/data/health.db"})
    data, why = tri.latest_verdict(require_today=True)
    assert data == {}, "критический вердикт о чужой базе повесил бы шапку не тому человеку"
    assert "/tenants/other/data/health.db" in why, \
        "причина обязана называть ЧЬЮ базу судил прогон — иначе отказ не разобрать"


def test_no_provenance_verdict_is_refused(artefact):
    """C-19: артефакт без db_path (до-C-19 формат) — провенанс неизвестен; тихо
    считать его «своим» = тихий дефолт, запрещённый multitenancy
    (tenant_data_loud_default)."""
    artefact(json.dumps({"date": str(TODAY), "critical": [["датчик", "деталь"]]}))
    data, why = tri.latest_verdict(require_today=True)
    assert data == {} and "без провенанса" in why


def test_no_artefact_is_a_reason_not_a_silence(artefact):
    data, why = tri.latest_verdict(require_today=True)
    assert data == {} and "не найден" in why


def test_unparsable_artefact_is_a_reason(artefact):
    artefact("{битый")
    data, why = tri.latest_verdict(require_today=True)
    assert data == {} and "не разбирается" in why


def test_yesterday_verdict_is_refused_when_today_required(artefact):
    y = TODAY - timedelta(days=1)
    artefact({"date": str(y), "critical": [["датчик", "деталь"]]})
    data, why = tri.latest_verdict(require_today=True)
    assert data == {}, "вчерашний вердикт не годится для блокировки сегодняшнего брифа"
    assert str(y) in why, "причина обязана называть дату — иначе она не отличима от «файла нет»"


def test_today_verdict_passes_through(artefact):
    artefact({"date": str(TODAY), "critical": [["датчик", "деталь"]]})
    data, why = tri.latest_verdict(require_today=True)
    assert why == "" and len(data["critical"]) == 1


def test_triage_takes_stale_artefact_on_purpose(artefact):
    """Триаж чинит, а не блокирует: граница свежести — не его дело."""
    artefact({"date": "2020-01-01", "warnings": [], "failures": []})
    data, why = tri.latest_verdict()
    assert why == "" and data["date"] == "2020-01-01"


def _wire_brief(monkeypatch, tmp_path, verdict):
    """Бриф с подменённым вердиктом; возвращает (отправленное, построен_ли_отчёт).

    HEALTH_DATA_DIR уводится в tmp: незаблокированный бриф ПИШЕТ отчёт в
    data/reports/<дата>.md. Первый прогон это и доказал — в staging появился
    2026-08-12.md, и на нём покраснел чужой doc-инвентарь. Тест, гадящий в
    каталог данных, отравляет соседей (§20).
    """
    sent, built = [], []

    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sched, "get_chat_id", lambda: 111222333)
    monkeypatch.setattr(sched, "refresh_data", lambda *a, **k: None)
    monkeypatch.setattr(tri, "latest_verdict", lambda require_today=False: verdict)

    def fake_report(target, sink=None):
        built.append(target)
        return "текст отчёта", None

    monkeypatch.setattr(sched.gp, "generate_daily_report", fake_report)

    async def fake_send_long(bot, chat_id, text, **kw):
        sent.append(text)

    monkeypatch.setattr(sched, "send_long", fake_send_long)
    return sent, built


_CRIT = [["SQLite integrity_check ok per-tenant", "tenant=owner: ГЕМОГЛОБИН 73 г/л"]]


def test_fresh_critical_marks_the_brief_but_lets_it_out(monkeypatch, tg, tmp_path):
    sent, built = _wire_brief(monkeypatch, tmp_path,
                              ({"date": str(TODAY), "critical": _CRIT}, ""))
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))

    assert built, "метка не отменяет наблюдение: отчёт обязан выйти"
    assert sent[0].startswith("🛑"), "шапка идёт ПЕРВОЙ, раньше отчёта и срочных алертов"
    # Решение владельца 28.09: имя датчика человеку ничего не говорит — оно в файле
    # отчёта и у оператора (триаж), а человеку — что цифрам сегодня верить осторожно.
    assert "SQLite integrity_check" not in sent[0]
    saved = (tmp_path / "data" / "reports" / f"{sched.get_today()}.md").read_text()
    assert "SQLite integrity_check" in saved
    assert "текст отчёта" in sent[-1]


def test_header_does_not_leak_sensor_detail(monkeypatch, tg, tmp_path):
    """§19: detail несёт значения анализов, шапка — только имена датчиков."""
    sent, _ = _wire_brief(monkeypatch, tmp_path,
                          ({"date": str(TODAY), "critical": _CRIT}, ""))
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))
    assert "73" not in sent[0] and "ГЕМОГЛОБИН" not in sent[0]


def test_header_is_plain_text(monkeypatch, tg, tmp_path):
    """Имена датчиков несут `_` (lab_results) — под Markdown это открытие italic,
    и валится доставка. Шапка обязана идти parse_mode=None (инцидент 03.07)."""
    kwargs = []

    async def spy(bot, chat_id, text, **kw):
        kwargs.append((text, kw))

    _wire_brief(monkeypatch, tmp_path, ({"date": str(TODAY), "critical": _CRIT}, ""))
    monkeypatch.setattr(sched, "send_long", spy)
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))
    # Именно «ключ передан и равен None»: у send_long дефолт — Markdown, поэтому
    # .get(...) is None зеленело бы и когда parse_mode не передан вовсе.
    assert "parse_mode" in kwargs[0][1] and kwargs[0][1]["parse_mode"] is None


def test_header_is_saved_into_the_report_file(monkeypatch, tg, tmp_path):
    """Файл читают дашборд и GP-циклы позже — метка обязана быть в самом документе."""
    _wire_brief(monkeypatch, tmp_path, ({"date": str(TODAY), "critical": _CRIT}, ""))
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))

    saved = (tmp_path / "data" / "reports" / f"{sched.get_today()}.md").read_text()
    assert saved.startswith("🛑") and "текст отчёта" in saved


def test_stale_verdict_leaves_no_mark(monkeypatch, tg, tmp_path):
    """Монитор молчит → метки нет, но и бриф не задержан. Причина уходит в лог."""
    sent, built = _wire_brief(monkeypatch, tmp_path,
                              ({}, "вердикт от 2026-08-11, а не за сегодня"))
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))

    assert built and not any(t.startswith("🛑") for t in sent)


def test_clean_verdict_leaves_no_mark(monkeypatch, tg, tmp_path):
    sent, built = _wire_brief(monkeypatch, tmp_path,
                              ({"date": str(TODAY), "critical": []}, ""))
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))
    assert built and not any(t.startswith("🛑") for t in sent)


def test_header_names_what_it_hides():
    """Усечение до пяти не должно читаться как «столько и было»."""
    many = [[f"датчик {i}", ""] for i in range(8)]
    head = sched.critical_header_file(many)
    assert "(8)" in head and "ещё 3" in head
    assert sched.critical_header_file([]) == ""
    person = sched.critical_header(many)
    assert person and "датчик" not in person and "run_checks" not in person


# ── Сам механизм пометки: check(critical=True) ────────────────────────────────
# integrity_tests импортировать нельзя — 134 регистрации исполняются НА ИМПОРТЕ
# (замер: 46,3 с). Поэтому берём из файла ровно одну функцию и исполняем её в
# подготовленном пространстве имён: это настоящий вызов настоящего кода, а не
# чтение текста. Регресс, который тест ловит: возврат sys.exit(2) в check() —
# тогда прогон снова оборвётся посреди, критические не доедут до артефакта, и
# метка тихо перестанет появляться (ровно как было до 12.08).

import ast
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "integrity_tests.py"


def _load_check():
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "check")
    ns = {"PASS": 0, "FAIL": 0, "JSON_OUTPUT": True, "WARN_ONLY": False,
          "_failures": [], "_code_failures": [], "_critical": [], "sys": sys,
          "_sensor_of": {}, "_current_sensor": ""}   # repair-order: check() пишет, какой датчик упал
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(_SRC), "exec"), ns)
    return ns


def test_critical_marks_and_does_not_abort_the_run():
    ns = _load_check()

    def boom():
        raise AssertionError("шкала референса разошлась")

    ns["check"]("критический датчик", boom, critical=True)   # SystemExit ⇒ красный
    ns["check"]("обычный датчик", boom)
    ns["check"]("живой датчик", lambda: 42)

    assert [l for l, _ in ns["_critical"]] == ["критический датчик"]
    assert ns["FAIL"] == 2 and ns["PASS"] == 1, "прогон обязан дойти до конца"


def test_critical_catches_code_errors_too():
    ns = _load_check()

    def typo():
        raise NameError("_tenant_db_pathz")

    ns["check"]("критический датчик", typo, critical=True)
    assert len(ns["_critical"]) == 1 and len(ns["_code_failures"]) == 1


def test_artefact_carries_the_critical_list():
    """Пометка бесполезна, если не доезжает до читателя."""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    keys = [k.value for n in ast.walk(tree) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant) and k.value == "critical"]
    assert keys, "JSON-артефакт монитора обязан нести ключ 'critical'"
