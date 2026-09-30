"""Целевые тесты гейта квитанции замысла (project_context/intentgate.py, нить intent-receipts).

Каждое правило R1-R5 проверяется РАЗДЕЛЬНО (урок disposability_gate::liveness_not_rule_coverage:
ночная проба ловит только грубого нарушителя — смерть отдельного правила ловят только эти тесты).
Негативные контроли ИСПОЛНЯЮТСЯ, не декларируются: валидная квитанция обязана проходить,
иначе детектор «ловит всё» и не ловит ничего.
Судья чистый (evaluate_files: файлы × реестр → вердикт) — git и диск здесь не трогаются.
"""
import pytest

from project_context import intentgate

REG = {
    "alpha": {"a_holds": "holds", "a_open": "open"},
    "beta": {"b1": "holds"},
}


def _plan(receipt_yaml, path="plans/PLAN_x_2026-08-07.md"):
    text = f"# План\n\n## Замысел\n\n```yaml\n{receipt_yaml}```\n\n## План\n\nэтапы\n"
    return {path: text}


VALID = "intent:\n  - id: alpha\n    invariants: {a_holds: holds, a_open: open}\nread_at: 2026-08-07\n"


# ── негативный контроль детектора: валидное ПРОХОДИТ ─────────────────────────

def test_valid_full_receipt_passes():
    blocks, warns = intentgate.evaluate_files(_plan(VALID), REG)
    assert blocks == [] and warns == []


def test_valid_none_with_reason_passes():
    blocks, _ = intentgate.evaluate_files(
        _plan("intent: none\nreason: правка только скиллов, кода проекта не касается\n"), REG)
    assert blocks == []


# ── R1: секция и разбор ──────────────────────────────────────────────────────

def test_r1_missing_section_blocks():
    files = {"plans/PLAN_x_2026-08-07.md": "# План\n\n## План\n\nбез замысла\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert len(blocks) == 1 and "R1" in blocks[0] and "## Замысел" in blocks[0]


def test_r1_section_without_intent_key_blocks():
    blocks, _ = intentgate.evaluate_files(_plan("прочитал: вроде да\n"), REG)
    assert len(blocks) == 1 and "R1" in blocks[0]


def test_r1_intent_not_list_blocks():
    blocks, _ = intentgate.evaluate_files(_plan("intent: alpha\nread_at: 2026-08-07\n"), REG)
    assert blocks and "R1" in blocks[0]


def test_r1_full_requires_invariant_statuses():
    # ids без статусов — не подпись чтения: полная форма требует живое состояние.
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: alpha\nread_at: 2026-08-07\n"), REG)
    assert blocks and "R1" in blocks[0] and "invariants" in blocks[0]


# ── R2: несуществующее ───────────────────────────────────────────────────────

def test_r2_unknown_subsystem_blocks():
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: gamma\n    invariants: {x: holds}\nread_at: 2026-08-07\n"), REG)
    assert len(blocks) == 1 and "R2" in blocks[0] and "gamma" in blocks[0]


def test_r2_unknown_invariant_blocks():
    # полный снимок + лишний призрак: краснеет ровно R2, не полнота
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: alpha\n    invariants: {a_holds: holds, a_open: open, ghost: holds}\nread_at: 2026-08-07\n"), REG)
    assert len(blocks) == 1 and "R2" in blocks[0] and "ghost" in blocks[0]


# ── R3: главное обещание — подделанный/протухший статус красный ─────────────

def test_r3_forged_status_blocks_with_both_versions():
    # полный снимок с одной подделкой: краснеет ровно R3
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: alpha\n    invariants: {a_holds: holds, a_open: holds}\nread_at: 2026-08-07\n"), REG)
    assert len(blocks) == 1 and "R3" in blocks[0]
    # сообщение обязано вести к действию: обе версии видны (один вопрос — один ключ)
    assert "holds" in blocks[0] and "open" in blocks[0]


# ── R6: полнота снимка (внешнее ревью 2026-08-07, F-01) ─────────────────────

def test_r6_empty_invariants_map_blocks():
    # Пустой словарь — валидный yaml и нулевая подпись чтения; до ремонта проходил hook с rc=0.
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: alpha\n    invariants: {}\nread_at: 2026-08-07\n"), REG)
    assert len(blocks) == 1 and "R6" in blocks[0]
    assert "a_holds" in blocks[0] and "a_open" in blocks[0]  # отсутствующие названы поимённо


def test_r6_subset_blocks_naming_missing():
    # «Один удобный holds» — подмножество не является снимком записи.
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: alpha\n    invariants: {a_holds: holds}\nread_at: 2026-08-07\n"), REG)
    assert len(blocks) == 1 and "R6" in blocks[0] and "a_open" in blocks[0]


def test_r6_exact_full_map_passes():
    # Позитивный контроль полноты: ровно все инварианты записи — проходит.
    blocks, _ = intentgate.evaluate_files(_plan(VALID), REG)
    assert blocks == []


def test_r6_review_subset_still_legal():
    # Инверсия периметра: review-форма полноты НЕ требует (ids без статусов легальны).
    files = {"docs/handoff/x/2026-08-07-abc1234.review.md":
             "# REVIEW\n\n## Замысел\n\n```yaml\nintent:\n  - id: alpha\n    invariants: {a_holds: holds}\n```\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert blocks == []


# ── R4: дата замера ──────────────────────────────────────────────────────────

def test_r4_missing_read_at_blocks():
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: beta\n    invariants: {b1: holds}\n"), REG)
    assert len(blocks) == 1 and "R4" in blocks[0]


def test_r4_garbage_read_at_blocks():
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: beta\n    invariants: {b1: holds}\nread_at: вчера\n"), REG)
    assert len(blocks) == 1 and "R4" in blocks[0]


def test_r4_unquoted_yaml_date_passes():
    # yaml отдаёт date-объект, не строку — это валидная ISO-дата, не мусор
    blocks, _ = intentgate.evaluate_files(
        _plan("intent:\n  - id: beta\n    invariants: {b1: holds}\nread_at: 2026-08-07\n"), REG)
    assert blocks == []


# ── R5: none и no_registry ───────────────────────────────────────────────────

def test_r5_none_without_reason_blocks():
    blocks, _ = intentgate.evaluate_files(_plan("intent: none\n"), REG)
    assert len(blocks) == 1 and "R5" in blocks[0]


def test_r5_no_registry_blocks_here():
    blocks, _ = intentgate.evaluate_files(_plan("intent: no_registry\n"), REG)
    assert len(blocks) == 1 and "R5" in blocks[0]


# ── периметр ─────────────────────────────────────────────────────────────────

def test_perimeter_pointers_and_foreign_files_skipped():
    files = {
        "docs/handoff/INDEX.md": "# индекс без замысла\n",
        "docs/handoff/x/LATEST.md": "указатель\n",
        "docs/explanation/foo.md": "тёплая страница\n",
        "README.md": "не артефакт закрытия\n",
        # вердикт внешней стороны — не авторский артефакт закрытия (живой кейс 07.08)
        "docs/handoff/x/2026-08-07-abc1234.review-findings.md": "# REVIEW FINDINGS\n",
        "docs/handoff/x/2026-08-07-abc1234.findings.md": "# вердикт\n",
    }
    blocks, warns = intentgate.evaluate_files(files, REG)
    assert blocks == [] and warns == []


def test_perimeter_handoff_snapshot_judged_full():
    files = {"docs/handoff/x/2026-08-07-abc1234.md": "# HANDOFF\n\nбез секции\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert len(blocks) == 1 and "R1" in blocks[0]


def test_perimeter_neighbour_note_in_inbox_is_not_a_closing_artifact():
    """Записка соседу (§22, адрес с 16.09) квитанции замысла не несёт.

    Её пишет ЧУЖАЯ нить о ЧУЖОЙ работе и ничего ею не закрывает; требовать с неё
    авторский снимок реестра — то же самое, что красить вердикт ревьюера.
    Найдено догфудингом на первой живой записке: гейт заблокировал коммит, который
    вводил сам новый адрес. Пара со снимком выше существенна — она стережёт, чтобы
    исключение не съело периметр целиком: снимок в том же каталоге судится по-прежнему.
    """
    files = {"docs/handoff/x/inbox/соседняя-нить.md": "# записка\n\nберу файлы A и B\n"}
    blocks, warns = intentgate.evaluate_files(files, REG)
    assert blocks == [] and warns == [], (
        f"записка соседу попала в периметр артефактов закрытия: {blocks} {warns}")


# ── ревью-форма: ids без статусов легальны, подделка — нет ───────────────────

def test_review_ids_without_statuses_pass():
    files = {"docs/handoff/x/2026-08-07-abc1234.review.md":
             "# REVIEW\n\n## Замысел\n\n```yaml\nintent:\n  - id: alpha\n```\n\n## Оракулы\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert blocks == []


def test_review_forged_status_still_blocks():
    files = {"docs/handoff/x/2026-08-07-abc1234.review.md":
             "# REVIEW\n\n## Замысел\n\n```yaml\nintent:\n  - id: alpha\n    invariants: {a_open: holds}\n```\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert len(blocks) == 1 and "R3" in blocks[0]


def test_review_missing_section_blocks():
    files = {"docs/handoff/x/2026-08-07-abc1234.review.md": "# REVIEW\n\nисполняй\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert len(blocks) == 1 and "R1" in blocks[0]


# ── реестр ───────────────────────────────────────────────────────────────────

def test_registry_loader_rejects_non_list():
    with pytest.raises(ValueError):
        intentgate._load_registry("just: a mapping\n")


def test_registry_loader_builds_status_map():
    reg = intentgate._load_registry(
        "- id: s1\n  invariants:\n  - id: i1\n    status: holds\n  - id: i2\n    status: open\n")
    assert reg == {"s1": {"i1": "holds", "i2": "open"}}


def test_multiple_files_all_judged():
    files = {}
    files.update(_plan(VALID))
    files.update(_plan("intent: none\n", path="plans/PLAN_y_2026-08-07.md"))
    blocks, _ = intentgate.evaluate_files(files, REG)
    assert len(blocks) == 1 and "PLAN_y" in blocks[0]
