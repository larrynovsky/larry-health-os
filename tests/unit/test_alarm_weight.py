"""Тяжесть и контекст тревог: сообщение не врёт, отказ провайдера виден (2026-08-11).

Замер, ради которого правка: сообщение «🚨 утренний отчёт заблокирован» приходило
каждое утро при любом FAIL, а блокировки не существует — `/tmp/health_integrity_status`
писался и не читался НИГДЕ, бриф делает бот из своей очереди в 08:30 и о мониторе не
знает, и 11.08 бриф был построен и доставлен (BRIEF_GATE diag 08:30:22 и 08:30:26,
context_cards 18 карточек).

В тот же день в логе бота лежало «assemble_cards recovery провайдер упал:
AttributeError» — карточка восстановления не собиралась, и наружу не выходило ничего.
Настоящая деградация молчала, несуществующая блокировка кричала.

ЧТО ЭТИ КОНТРОЛИ НЕ ДОКАЗЫВАЮТ (RST, check vs test): что новое сообщение ПОНЯТНО
человеку. Это test с человеческим оракулом — читатель владелец, разово. Ниже только
checks: решающее правило, применённое к наблюдению.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _run_checks() -> str:
    return (ROOT / "run_checks.sh").read_text(encoding="utf-8")


def _code_lines(src: str) -> str:
    """Только исполняемые строки: комментарии ОБЪЯСНЯЮТ прежний дефект и обязаны
    называть его дословно — иначе объяснение нечитаемо. Сторожим код, не рассказ."""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_message_does_not_claim_a_blocking_that_never_happens():
    """⭐ Ровно дефект: сообщение утверждало последствие, которого нет."""
    body = _code_lines(_run_checks())
    assert "заблокирован" not in body, (
        "сообщение снова обещает блокировку утреннего отчёта — её не существует: "
        "бриф делает jobs/scheduled.py::send_morning_report, а exit 2 заканчивает "
        "только сам run_checks.sh")


def test_message_still_names_the_failures_loudly():
    """НЕГАТИВНЫЙ КОНТРОЛЬ к предыдущему: убрать ложь ≠ замолчать.

    Спокойным становится описание ПОСЛЕДСТВИЯ, а не сама находка. Если из текста
    исчезнет число FAIL, правка превратится в глушение — то, чего мы боялись
    больше самой лжи (R-1 плана).
    """
    body = _code_lines(_run_checks())
    assert "FAIL_COUNT" in body and "record_fault" in body
    assert "${FAIL_COUNT}" in body, "число падений обязано оставаться в сообщении"


def test_orphan_state_file_is_not_written_again():
    """Осиротевшее состояние в /tmp удалено, а не наделено читателем.

    Возврат записи — это возврат сразу двух дефектов: файл в общем каталоге без
    проверки прав (WSTG-CONF-09) и состояние без границы свежести, которое
    читается как «всё хорошо», когда монитор просто не отработал.
    """
    body = _code_lines(_run_checks())
    assert "/tmp/health_integrity_status" not in body, (
        "запись состояния в /tmp вернулась: у файла нет ни прав, ни времени, "
        "а читатель принял бы вчерашний OK за сегодняшний")


def test_recovery_series_reaches_the_facade():
    """Имя доехало до фасада — то, чего не хватало."""
    import health_ai
    assert hasattr(health_ai, "recovery_series"), (
        "brief_pipeline зовёт hai.recovery_series; без реэкспорта это AttributeError, "
        "проглоченный except'ом")
    assert callable(health_ai.recovery_series)


def test_brief_pipeline_calls_the_name_it_imports():
    """СКВОЗНОЙ контроль стыка, где дефект и жил.

    Предыдущий тест доказывает наличие имени в фасаде, этот — что зовут именно
    его. По отдельности обе стороны были исправны: функция существовала в
    hai_analysis, вызов существовал в brief_pipeline, а между ними фасад без
    реэкспорта. Дефекты живут на стыках, и сторожить надо стык.
    """
    import health_ai
    import hai_analysis
    src = (ROOT / "brief_pipeline.py").read_text(encoding="utf-8")
    assert "hai.recovery_series(" in src, "вызов исчез — тест выше стал бессмысленным"
    assert health_ai.recovery_series is hai_analysis.recovery_series, (
        "фасад отдаёт НЕ ту функцию — реэкспорт разъехался с домом")


def test_every_swallowed_provider_failure_is_recorded():
    """Каждый `except` провайдера обязан оставить след наружу.

    Ратчет формы: сколько строк «провайдер упал» — столько и вызовов учёта.
    Появится новый провайдер с проглатыванием и без записи — тест покраснеет,
    и отказ не повторит судьбу recovery, молчавшего неизвестно сколько дней.
    """
    src = (ROOT / "brief_pipeline.py").read_text(encoding="utf-8")
    swallowed = src.count("провайдер упал: %r")
    recorded = src.count("_record_provider_failure(")
    assert swallowed > 0, "фикстура протухла: строки об отказе провайдеров исчезли"
    # -1: определение самой функции тоже содержит её имя
    assert recorded - 1 >= swallowed, (
        f"проглоченных отказов {swallowed}, записей наружу {recorded - 1} — "
        f"какой-то провайдер снова падает молча")


def test_sensor_speaks_only_about_today(tmp_path, monkeypatch):
    """Артефакт «про сегодня»: вчерашние отказы молчат.

    Иначе датчик кричал бы о починенном ещё сутки, и WARN потерял бы смысл
    «сегодня сломано».
    """
    import integrity_tests as I
    logs = tmp_path / "logs"
    logs.mkdir()
    said = []
    monkeypatch.setattr(I, "warn", lambda n, d="": said.append(n))
    monkeypatch.setattr(I, "Path", Path)
    art = logs / "brief_provider_failures.json"

    def _run(payload):
        art.write_text(json.dumps(payload), encoding="utf-8")
        said.clear()
        # подменяем корень, откуда датчик берёт артефакт
        real_file = I.__file__
        monkeypatch.setattr(I, "__file__", str(tmp_path / "integrity_tests.py"))
        try:
            return I._brief_provider_failures_warn()
        finally:
            monkeypatch.setattr(I, "__file__", real_file)

    r = _run({"date": "1999-01-01", "failures": {"recovery": "AttributeError"}})
    assert r == {"provider_failures": 0} and said == [], "вчерашние отказы шумят"

    r = _run({"date": str(I.today), "failures": {"recovery": "AttributeError"}})
    assert r == {"provider_failures": 1}, r
    assert said and "провайдер" in said[0], said
