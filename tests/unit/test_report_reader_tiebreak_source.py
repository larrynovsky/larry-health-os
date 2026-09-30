"""Source-guard: КАЖДЫЙ читатель agent_reports.longitudinal_analysis с ORDER BY … LIMIT 1
обязан нести тай-брейк `id DESC` (read-your-writes, R2, 2026-07-12).

ЗАЧЕМ отдельно от поведенческого теста (test_longitudinal_delivery_read_your_writes):
позит-контроль 2026-07-13 показал структурный факт — читатели по `date DESC` идут через
индекс (agent_type,date), и SQLite на текущем движке возвращает max-rowid (= последнюю
запись) ДАЖЕ без тай-брейка. То есть их регрессию (снятие id DESC) НЕЛЬЗЯ детерминированно
спровоцировать данными: неспецифицированный порядок совпал с нужным. Поведенческий тест
надёжно бьёт лишь по `created_at`-читателю (без индекса → sort вернул старую строку).
Значит тай-брейк date-читателей стережётся только на уровне ИСТОЧНИКА. Это не дубль SQL
(антипаттерн старого test_report_read_total_order — он ГОНЯЛ копию строки), а чтение
прод-файла и проверка присутствия инварианта в нём.

Перепись, не список: сканируем весь код, находим ЛЮБОЙ longitudinal-читатель с ORDER BY…LIMIT 1
и требуем id DESC. Новый (4-й) читатель без тай-брейка → тест краснеет (закрывает census-gap).
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# Известные читатели (позитивный контроль полноты: перепись обязана их найти).
# 2026-07-26: оба ИИ-читателя (gp_context, generate_constitutions) свой SQL больше НЕ пишут —
# приёмка веры централизована в belief_contract.read_belief (fail-closed, P1-01). Их имена
# перенесены в _MUST_NOT_READ_DIRECTLY ниже: собственный SQL у них теперь = обход контракта.
_KNOWN_READERS = {"belief_contract.py", "integrity_tests.py"}

# Файлы, которым запрещено читать веру мимо контракта: свой SQL = своё правило приёмки,
# а правил приёмки должно быть ровно одно.
_MUST_NOT_READ_DIRECTLY = {"gp_context.py", "generate_constitutions.py"}

_ANCHOR = re.compile(r"agent_type\s*=\s*'longitudinal_analysis'")


def _ordered_reads(text: str) -> list[str]:
    """Окна за каждым longitudinal-фильтром, где дальше идёт ORDER BY … LIMIT 1.

    Возвращает тексты таких окон (сам ORDER BY…LIMIT-фрагмент). MAX(date)-запросы
    свежести/survivorship не имеют ORDER BY+LIMIT 1 → не попадают.
    """
    windows: list[str] = []
    for m in _ANCHOR.finditer(text):
        win = text[m.end(): m.end() + 300]
        om = re.search(r"ORDER BY\s+(?:date|created_at)\s+DESC.{0,80}?LIMIT 1", win, re.S)
        if om:
            windows.append(om.group(0))
    return windows


def _iter_source_files():
    for p in _REPO.glob("*.py"):
        yield p


def test_every_longitudinal_reader_has_id_desc_tiebreak():
    offenders: list[str] = []
    found_readers: set[str] = set()
    for p in _iter_source_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for win in _ordered_reads(text):
            found_readers.add(p.name)
            if "id DESC" not in win:
                offenders.append(f"{p.name}: longitudinal-читатель без id DESC → «{win.strip()}»")

    assert not offenders, (
        "Читатель agent_reports.longitudinal_analysis без тай-брейка id DESC — "
        "read-your-writes сломан (при 2+ строках/дату вернётся произвольная):\n  "
        + "\n  ".join(offenders)
    )
    # Перепись обязана видеть всех известных читателей — иначе якорь/regex устарел и молча
    # перестал стеречь (аналог мёртвого сторожа).
    missing = _KNOWN_READERS - found_readers
    assert not missing, (
        f"Перепись не нашла известных читателей {missing} — anchor-regex устарел, "
        "источник переписан? Обнови детектор, иначе тай-брейк больше не стережётся."
    )


def test_ai_readers_do_not_bypass_belief_contract():
    """ИИ-читатели обязаны идти через belief_contract, а не читать веру своим SQL.

    Свой SQL у читателя = своё правило приёмки. Именно так и возникла P1-01: правило
    «гейт обязан быть применён» существовало в докстрингах, а каждый читатель брал
    последнюю строку как есть. Один источник правила — или его нет.
    """
    offenders = []
    for name in _MUST_NOT_READ_DIRECTLY:
        p = _REPO / name
        if not p.exists():
            continue
        if _ANCHOR.search(p.read_text(encoding="utf-8")):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} читают agent_reports.longitudinal_analysis напрямую — обход "
        "belief_contract.read_belief. Вера принимается только через контракт."
    )
