"""Границы срезов, которые едут в промпты, ОБЪЯВЛЕНЫ, а не подразумеваются.

Класс (report_absence_claims): между базой и промптом стоит фильтр, и отсутствие
строки в показанном срезе модель читает как отсутствие анализа. До 14.09 окно
объявлял только лаб-блок GP; профиль пациента (список из 20 имён) и лаб-блок чата
(усечение до 25 строк) не объявляли ничего, а заголовок чата обещал период целиком.

Размер корпуса не определяет полноту показанного среза. Каждый фильтр должен
объявить свою границу; мутации ниже проверяют это независимо от личных данных.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── labs_db.canon_window_note: второй фильтр объявляется ─────────────────────

def test_note_declares_subset_when_shown_tests_given(db, clock):
    """Мутация: убрать shown_tests из вызова → объявление молчит о подмножестве."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import labs_db
    note = labs_db.canon_window_note(180, shown_tests=["HbA1c"])
    assert "ПОДМНОЖЕСТВО" in note          # второй фильтр назван
    assert "1 " in note and "2 " in note   # показано 1 из 2
    assert "TSH" not in note               # имена невидимого НЕ едут (§19)


def test_note_declares_both_filters_when_old_rows_exist(db, clock):
    """Ветка «есть строки старше окна» ТОЖЕ обязана назвать подмножество.

    Отдельный тест, потому что у функции две ветки возврата, и зелёная одна из них
    ничего не говорит о второй (мутация «убрать list_note из длинной ветки» роняет
    только этот тест).
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    db.add_lab_result("2021-06-14", "Insulin", 6.3, unit="мкЕд/мл")
    import labs_db
    note = labs_db.canon_window_note(180, shown_tests=["HbA1c"])
    assert "2021-06-14" in note            # граница окна названа датой
    assert "Insulin" not in note           # имя за окном не едет
    assert "ПОДМНОЖЕСТВО" in note          # и второй фильтр тоже назван


def test_note_silent_about_subset_when_all_shown(db, clock):
    """Позитивный контроль: список покрывает всё окно → про подмножество молчим."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    import labs_db
    note = labs_db.canon_window_note(180, shown_tests=["HbA1c"])
    assert "ПОДМНОЖЕСТВО" not in note


def test_note_never_says_whole_canon_when_list_hides_rows(db, clock):
    """Фраза «показан ВЕСЬ канон» при работающем списке имён — ложь.

    Мутация: вернуть старую ветку без проверки list_note → тест краснеет.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import labs_db
    note = labs_db.canon_window_note(180, shown_tests=["HbA1c"])
    assert "ВЕСЬ канон" not in note


# ── patient_context: профиль объявляет границу и не молчит при сбое ──────────

def test_patient_brief_labs_block_declares_boundary(db, clock):
    """Снимок анализов в профиле несёт объявление границы.

    Мутация: убрать вызов canon_window_note → краснеет.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import health_db, patient_context
    block = patient_context._labs_snapshot(health_db)
    assert "HbA1c" in block
    assert "ГРАНИЦА" in block


def test_labs_snapshot_failure_is_loud(clock):
    """Сбой чтения даёт МАРКЕР, а не пустую строку.

    Пустая строка = исчезнувший раздел, а исчезнувший раздел читается моделью как
    «анализов нет вовсе». Мутация: вернуть `return ""` → краснеет.
    """
    clock.set("2026-09-14")
    import patient_context

    class _Broken:
        def get_recent_labs(self, *a, **kw):
            raise RuntimeError("БД недоступна")

    out = patient_context._labs_snapshot(_Broken())
    assert out != ""
    assert "недоступен" in out
    assert "НИЧЕГО не говорит" in out


# ── hai_context: лаб-блок чата объявляет усечение ───────────────────────────

def test_chat_lab_block_declares_truncation(db, clock, monkeypatch):
    """Блок «последние 2 года» с потолком 25 обязан сказать, что показал не всё.

    Потолок опускается до 1 монкипатчем — иначе фикстуре пришлось бы завести 26
    аналитов ради одного утверждения. Мутация: убрать объявление → краснеет.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import hai_context
    monkeypatch.setattr(hai_context, "_LABS_SHOWN_MAX", 1)
    out = hai_context.build_smart_context("какие у меня анализы")
    assert "Лабораторные данные" in out
    assert "ГРАНИЦА" in out
    assert "ПОДМНОЖЕСТВО" in out


# ── 21.09: блоки, которые молчали (периметр вычислен, а не вспомнен) ─────────

def test_lab_history_context_declares_what_it_drops(db, clock):
    """Блок без окна отбирает именами (min_points, только числовые) и обязан это сказать.

    Мутация: убрать вызов declared_boundary из build_lab_history_context → краснеет.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")           # свежее → показано
    db.add_lab_result("2021-06-14", "Insulin", 6.3, unit="мкЕд/мл")   # 1 давняя точка → нет
    import labs_db
    ctx = labs_db.build_lab_history_context()
    assert "HbA1c" in ctx and "Insulin" not in ctx
    assert "ГРАНИЦА БЛОКА" in ctx
    assert "1 имён из 2" in ctx             # счёт сходится с фикстурой
    assert "не измерено" in ctx             # названо, какое чтение запрещено


def test_lab_history_context_says_whole_canon_when_nothing_dropped(db, clock):
    """Позитивный контроль: ничего не отброшено → про подмножество ни слова."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    import labs_db
    ctx = labs_db.build_lab_history_context()
    assert "ВЕСЬ канон" in ctx and "НЕ попали" not in ctx


def test_declared_boundary_failure_is_said_in_text(monkeypatch):
    """Отказ сборки объявления сам становится объявлением, а не пустой строкой."""
    import labs_db
    monkeypatch.setattr(labs_db, "canon_window_note",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert "НЕПОЛНЫМ" in labs_db.declared_boundary(180)


def test_chat_tool_resolves_name_and_never_claims_absence(db, clock):
    """Русский синоним находит каноническое имя на вымышленном входе.

    Мутация: вернуть `tests` вместо `wanted` в get_recent_labs → краснеет.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2021-06-14", "TSH", 2.35, unit="мМЕ/л")
    import hai_context
    out = hai_context._execute_tool("query_labs", {"tests": ["ТТГ"]})
    assert "TSH: 2.35" in out
    miss = hai_context._execute_tool("query_labs", {"tests": ["Квазианалит"]})
    assert "Нет лабораторных данных" not in miss
    assert "НЕ значит" in miss and "Квазианалит" in miss


def test_chat_tool_prints_text_result_not_none(db, clock):
    """Качественный результат живёт в value_text; печать одной `value` давала `None`."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "Urine_Bilirubin", None, value_text="negative")
    import hai_context
    out = hai_context._execute_tool("query_labs", {"tests": ["Urine_Bilirubin"]})
    assert "None" not in out and "отрицательно" in out


def _broken_note(monkeypatch):
    import labs_db

    def _boom(*a, **kw):
        raise RuntimeError("сборка объявления упала")
    monkeypatch.setattr(labs_db, "canon_window_note", _boom)


def test_gp_labs_block_survives_note_failure(db, clock, monkeypatch):
    """Отказ оговорки не роняет лаб-блок GP, а говорит о неполноте.

    Замер 21.09 на main: gp_context звал canon_window_note без защиты, и падение ОДНОЙ
    оговорочной фразы роняло весь контекст GP (вызывающий исключение не ловит).
    Мутация: вернуть прямой вызов canon_window_note → тест падает с RuntimeError.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "HbA1c", 5.74, unit="%")
    _broken_note(monkeypatch)
    import gp_context
    lines, _ = gp_context._build_labs_block()
    assert "НЕПОЛН" in "\n".join(lines)


def test_chat_lab_block_prints_text_result(db, clock):
    """Лаб-раздел чата печатает качественный результат словами, а не `None`."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "Urine_Bilirubin", None, value_text="negative")
    import hai_context
    out = hai_context.build_smart_context("какие у меня анализы")
    sec = out[out.find("Лабораторные данные"):]
    assert "Urine_Bilirubin" in sec and "None" not in sec and "отрицательно" in sec


def test_chat_lab_block_carries_per_analyte_freshness(db, clock):
    """27.09: свежесть в чате судится графиком контроля аналита (тот же блок, что у врача),
    а не общим «180 дней» из системного промпта. Мутация: убрать блок → краснеет."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import hai_context
    out = hai_context.build_smart_context("какие у меня анализы")
    assert "Свежесть по графику контроля" in out[out.find("Лабораторные данные"):]
