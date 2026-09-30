"""Забор без повтора: разовый анализ не исчезает из контекста (14.09).

Строка может исчезнуть из контекста, если `gp_context` режет по окну,
а `build_lab_history_context` — по `min_points=3`.
Невидимость для читателя не означает, что измерения не было.
"""
import io
import pathlib

import health_db  # noqa: F401  ПЕРВЫМ: config_db↔health_db↔labs_db — круговой импорт
import labs_db

ROOT = pathlib.Path(__file__).resolve().parents[2]  # …/health_scripts


def _row(name, date, value=None, text=None, lo=None, hi=None, unit=""):
    return {"test_name": name, "date": date, "value": value, "value_text": text,
            "unit": unit, "ref_low": lo, "ref_high": hi}


def test_analyte_with_fresh_measurement_is_not_a_candidate():
    """Пересдали — аналит покидает множество. Это и есть гаситель: расти само
    множество не может, поэтому порога показа у блока нет."""
    rows = [_row("Cholesterol", "2035-02-12", 7.4, lo=3.0, hi=5.0),
            _row("Cholesterol", "2042-09-01", 4.2, lo=3.0, hi=5.0)]
    d = labs_db.unrepeated_draw(rows, "2040-10-11")
    assert d["total"] == 0, "аналит со свежим замером не историчен"


def test_out_of_reference_is_named_and_normal_is_counted():
    # Независимо придуманные аналит, единицы и референсы; проверяется классификация.
    rows = [_row("SYNTH_OUTSIDE", "2035-02-12", 72.0, lo=10.0, hi=30.0, unit="demo"),
            _row("SYNTH_INSIDE", "2035-02-12", 22.0, lo=10.0, hi=30.0, unit="demo")]
    d = labs_db.unrepeated_draw(rows, "2040-10-11")
    assert d["normal"] == 1, "строка в норме бланка едет в счётчик, не поимённо"
    assert [r[0] for r in d["out_of_ref"]] == ["SYNTH_OUTSIDE"]


def test_qualitative_positive_is_named_but_negative_is_counted():
    """Найдено ≠ не найдено. Положительная скрытая кровь (синтетика той же
    формы) может не звучать нигде годами — ровно из-за того, что качественный результат не имеет
    референса и выпадал из всех предикатов «вне нормы»."""
    rows = [_row("Fecal_Hemoglobin", "2035-02-10", text="positive"),
            _row("Urine_Nitrite", "2035-02-12", text="negative"),
            _row("Urine_Salts", "2035-02-12", text="trace")]
    d = labs_db.unrepeated_draw(rows, "2040-10-11")
    assert d["qual_positive"] == [("Fecal_Hemoglobin", "положительно")]
    assert d["qual_other"] == 2, "«отрицательно» и «следы» — счётчик, не находка"


def test_value_without_reference_does_not_become_a_finding():
    """Нет референса — нет суждения «вне нормы». EBV в копиях ДНК (синтетика ~3e6) референса не
    имеет: назвать его отклонением значило бы выдумать порог (§9)."""
    rows = [_row("EBV_DNA", "2035-02-12", 3200000.0, unit="копий")]
    d = labs_db.unrepeated_draw(rows, "2040-10-11")
    assert d["no_ref"] == 1 and not d["out_of_ref"]


def test_block_is_wired_to_readers():
    """Датчик без доставки — системный анти-паттерн этого проекта. Блок обязан
    вызываться там, где собирается контекст: иначе он честно считает в пустоту.

    СУДИТСЯ НЕ СТРОКА, А РАЗРЕШИМОСТЬ ИМЕНИ. Первая версия теста искала подстроку
    в исходнике — то есть проверяла ПРИСУТСТВИЕ вызова, а не то, что он отработает.
    Цена ошибки замерена 15.09: `monthly_consilium` зовёт `db.build_unrepeated_draw_context`,
    где `db` — это `health_db`, а тот функцию не ре-экспортировал. Вызов падал
    в `except` с `log.warning`, консилиум блока не получал, и подстрочный тест был
    зелёным. Нашёл это контрактный ратчет соседней нити, не мой оракул.
    """
    import importlib
    import re

    # generate_constitutions снят 2026-09-25 решением владельца (горизонт конституции): разовый
    # забор — не устройство, у него нет ряда; его дом — GP и месячный консилиум. Обратное
    # (блок НЕ зовётся в конституции) держит tests/unit/test_constitution_horizon.py через
    # исходник ниже.
    for mod in ("gp_context.py", "monthly_consilium.py"):
        src = io.open(ROOT / mod, encoding="utf-8").read()
        m = re.search(r"(\w+)\.build_unrepeated_draw_context\s*\(", src)
        assert m, f"{mod} не зовёт блок"
        alias = m.group(1)
        # каким модулем читатель назвал этот алиас
        im = re.search(rf"^\s*import\s+(\w+)\s+as\s+{alias}\s*$", src, re.M) \
            or re.search(rf"^\s*import\s+({alias})\s*$", src, re.M)
        assert im, f"{mod}: алиас {alias!r} ни к какому import не привязан"
        target = importlib.import_module(im.group(1))
        assert hasattr(target, "build_unrepeated_draw_context"), (
            f"{mod} зовёт {alias}.build_unrepeated_draw_context, но "
            f"{im.group(1)} этого имени не отдаёт — вызов упадёт в except молча")


if __name__ == "__main__":
    test_analyte_with_fresh_measurement_is_not_a_candidate()
    test_out_of_reference_is_named_and_normal_is_counted()
    test_qualitative_positive_is_named_but_negative_is_counted()
    test_value_without_reference_does_not_become_a_finding()
    print("ok")


def test_one_sided_reference_never_prints_none():
    """`None` в промпте читается как значение, а не как «границы нет».

    Односторонний интервал бланка («< 50», «> 4.3») хранится NULL-ом на второй
    границе, и наивная f-строка выдавала «норма None–50.0». Замер на каноне
    14.09: таких строк в блоке нашлось несколько.
    """
    assert labs_db._ref_ru(None, 50.0) == "норма до 50.0"
    assert labs_db._ref_ru(4.3, None) == "норма от 4.3"
    assert labs_db._ref_ru(0.0, 20.0) == "норма 0.0–20.0"
    assert labs_db._ref_ru(None, None) == "референс не установлен"


def test_window_note_does_not_deny_names_that_the_block_prints():
    """Объявление границы и блок имён не противоречат друг другу в одном промпте.

    Замер 15.09: врач получал две строки подряд — «их имена здесь не
    перечислены» и следом «вне референса бланка (N): <аналит>=<значение>…». Одно
    утверждение в промпте отрицало соседнее. Класс тот же, ради которого стоит вся
    запись `report_absence_claims`: ложное утверждение о данных, дошедшее до читателя.
    """
    import re

    src = io.open(ROOT / "gp_context.py", encoding="utf-8").read()
    # читатель, который печатает имена, ОБЯЗАН объявить это в границе окна
    calls_block = "build_unrepeated_draw_context" in src
    # С 21.09 вызывающие зовут объявление через небросающую обёртку declared_boundary;
    # искать только прямое имя значило бы получить «не объявляет» на объявляющем коде.
    m = re.search(r"(?:canon_window_note|declared_boundary)\(([^)]*)\)", src)
    assert m, "gp_context не объявляет границу окна"
    # С 21.09 блок включается флагом `unrepeated`, и оговорка обязана следовать за тем же
    # флагом — литерал True/False здесь разошёлся бы с вызовом блока в одной из веток.
    assert calls_block == (m.group(1).replace(" ", "").endswith("named_below=unrepeated")
                           or "named_below=True" in m.group(1)), (
        "gp_context печатает имена из-за границы, но объявляет обратное "
        "(или наоборот) — промпт противоречит сам себе")


def test_readers_without_the_block_keep_the_unconditional_wording():
    """Позитивный контроль: тем, кто блок НЕ зовёт, текст менять нельзя — у них
    безоговорочное «имена не перечислены» верно."""
    judged = 0
    for mod in ("patient_context.py", "wellally_consult.py", "hai_context.py"):
        src = io.open(ROOT / mod, encoding="utf-8").read()
        # Обе формы вызова: после 21.09 в этих модулях осталась только declared_boundary,
        # и проверка по одному имени молча пропускала бы все три — зелёный без суда.
        if "canon_window_note" not in src and "declared_boundary" not in src:
            continue
        judged += 1
        assert "build_unrepeated_draw_context" not in src, (
            f"{mod} начал звать блок имён — объявление границы там тоже обязано "
            f"получить named_below=True")
        assert "named_below" not in src, (
            f"{mod} объявляет имена ниже, но блока не зовёт — объявление лжёт")
    assert judged == 3, f"судимо {judged} модулей из 3 — проверка пропустила объявляющий модуль"


def test_weekly_gp_has_no_block_and_note_does_not_promise_names(db, clock):
    """Решение владельца 21.09: в еженедельном контексте блока нет — и оговорка об этом не
    врёт; в месячном блок есть вместе с оговоркой «кроме тех, что названы поимённо».

    Мутация: `named_below=True` литералом → в недельной ветке оговорка обещает имена,
    которых нет, и тест краснеет. Мутация «флаг не прокинут» роняет вторую половину.
    """
    # Независимо придуманные результаты по обе стороны границы окна.
    clock.set("2042-10-21")
    db.add_lab_result("2042-10-01", "SYNTH_RECENT", 18.0, unit="demo")
    db.add_lab_result("2035-02-12", "SYNTH_OLD", 93.0, unit="demo", ref_low=None, ref_high=40.0)
    import gp_context
    week, _ = gp_context._build_labs_block(unrepeated=False)
    month, _ = gp_context._build_labs_block(unrepeated=True)
    w, m = "\n".join(week), "\n".join(month)
    assert "СДАНО ОДИН РАЗ" not in w and "названы поимённо" not in w
    assert "СДАНО ОДИН РАЗ" in m and "названы поимённо" in m


def test_weekly_report_asks_context_without_block(db, clock, monkeypatch):
    """Еженедельный отчёт GP просит контекст БЕЗ блока, месячный — с блоком.

    Флаг решает вызывающий; тест судит именно вызов, а не сборщик. Модель, сторож,
    сохранение и разбор problem list подменены — сети нет. Мутация: убрать
    `unrepeated=False` из generate_weekly_report → тест краснеет.
    """
    from datetime import date
    import gp_agent
    seen = []

    def _ctx(end_date, period_days=7, unrepeated=True):
        seen.append((period_days, unrepeated))
        return "контекст"

    class _Msg:
        content = [type("T", (), {"text": "отчёт"})()]

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return _Msg()

    monkeypatch.setattr(gp_agent, "_build_gp_context", _ctx)
    monkeypatch.setattr(gp_agent, "_get_client", lambda: _Client())
    monkeypatch.setattr(gp_agent, "_build_gp_system_prompt", lambda: "система")
    monkeypatch.setattr(gp_agent, "_build_gp_monthly_prompt", lambda: "система")
    monkeypatch.setattr(gp_agent, "_guard_absent_claims", lambda r, *a, **k: r)
    monkeypatch.setattr(gp_agent, "_save_gp_report", lambda *a, **k: None)
    monkeypatch.setattr(gp_agent, "_review_problem_list", lambda *a, **k: [])
    clock.set("2042-10-21")
    gp_agent.generate_weekly_report(date(2042, 10, 20), run_mdt=False)
    gp_agent.generate_monthly_report(date(2042, 10, 20))
    assert seen == [(7, False), (30, True)], seen
