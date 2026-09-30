#!/usr/bin/env python3.11
"""
generate_test.py — UC-J-02: подтверждение перед генерацией теста.

Принимает UC ID, читает USE_CASES.md, проверяет:
1. UC существует в каталоге §3.
2. `confirmation == confirmed` (иначе — отказ с объяснением).
3. Показывает план теста (что проверяем, оракул, моки) и ждёт `yes`.
4. Только после подтверждения — пишет skeleton-файл `tests/<layer>/test_uc_X_NN_*.py`.

CLI:
    python3.11 generate_test.py UC-A-04
    python3.11 generate_test.py UC-A-04 --auto-confirm   # для CI/Claude
    python3.11 generate_test.py UC-A-04 --dry-run        # показать план, не писать
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from doc_translation import strip_switch

SCRIPT_DIR = Path(__file__).parent
USE_CASES_MD = SCRIPT_DIR / "USE_CASES.md"
TESTS_DIR = SCRIPT_DIR / "tests"


# ── Парсинг каталога §3 USE_CASES.md ────────────────────────────────────────


_UC_TABLE_ROW = re.compile(
    r"^\|\s*`(?P<id>UC-[A-Z]-\d+|UC-X-\d+|UC-J-\d+|UC-K-\d+)`"
    r"\s*\|\s*(?P<pragmatic>[^|]*)"
    r"\s*\|\s*(?P<priority>P\d)"
    r"\s*\|\s*(?P<type>[^|]+)"
    r"\s*\|\s*(?P<status>implemented|partial|intended|speculative)"
    r"\s*\|\s*(?P<confirmation>proposed|confirmed|rejected)"
    r"\s*\|\s*$"
)


def parse_uc_catalog(use_cases_md: Path) -> dict[str, dict]:
    """Возвращает {uc_id: {pragmatic, priority, type, status, confirmation}}."""
    if not use_cases_md.exists():
        return {}
    catalog: dict[str, dict] = {}
    for line in strip_switch(use_cases_md.read_text(encoding="utf-8")).splitlines():
        m = _UC_TABLE_ROW.match(line.strip())
        if m:
            d = m.groupdict()
            uc_id = d.pop("id")
            for k in d:
                d[k] = d[k].strip()
            catalog[uc_id] = d
    return catalog


def get_uc(uc_id: str) -> dict | None:
    return parse_uc_catalog(USE_CASES_MD).get(uc_id)


# ── Layer detection ──────────────────────────────────────────────────────────


def layer_for_type(type_str: str) -> str:
    """type из каталога → имя подкаталога tests/."""
    t = type_str.lower().strip()
    if "manual_charter" in t:
        return "charters"
    if "llm_review" in t:
        return "llm_judge"
    if "e2e_mock" in t or "e2e" in t:
        return "e2e_mock"
    if "integration" in t:
        return "integration"
    return "unit"  # check / meta / default


# ── Skeleton-генератор ───────────────────────────────────────────────────────


SKELETON = '''"""
{uc_id} — {pragmatic}.

Источник: USE_CASES.md → {uc_id}.
Status: {status} · Confirmation: {confirmation}.
План: tests/plans/{uc_id}.md.

TODO: реализовать тесты.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.{layer_marker}


def test_{uc_id_snake}_placeholder():
    """TODO: заменить на реальные проверки из плана."""
    pytest.skip("Не реализовано — заглушка после generate_test.py")
'''


def build_skeleton(uc_id: str, uc: dict) -> str:
    layer = layer_for_type(uc["type"])
    layer_marker = "unit" if layer == "unit" else layer  # для @pytest.mark.X
    return SKELETON.format(
        uc_id=uc_id,
        pragmatic=uc["pragmatic"].strip().rstrip("."),
        status=uc["status"],
        confirmation=uc["confirmation"],
        layer_marker=layer_marker,
        uc_id_snake=uc_id.lower().replace("-", "_"),
    )


def target_path(uc_id: str, uc: dict) -> Path:
    layer = layer_for_type(uc["type"])
    safe_id = uc_id.lower().replace("-", "_")
    # Берём первое слово из прагматики как hint
    hint = re.sub(r"[^a-zа-я0-9]+", "_",
                  uc["pragmatic"].lower().split()[0] if uc["pragmatic"] else "")[:20]
    if not hint:
        hint = "unnamed"
    return TESTS_DIR / layer / f"test_{safe_id}_{hint}.py"


# ── План теста (что показать пользователю) ──────────────────────────────────


def format_plan(uc_id: str, uc: dict) -> str:
    layer = layer_for_type(uc["type"])
    target = target_path(uc_id, uc)
    return f"""
═══ Тест-план для {uc_id} ═══
Прагматика:    {uc['pragmatic']}
Priority:      {uc['priority']}
Type:          {uc['type']}
Status:        {uc['status']}
Confirmation:  {uc['confirmation']}

→ Слой:         {layer}
→ Файл:         {target.relative_to(SCRIPT_DIR)}
→ План:         tests/plans/{uc_id}.md (если существует — рекомендуется прочитать)

ОРАКУЛЫ для теста (из USE_CASES.md §4):
- B (negative invariants): что система НЕ должна делать
- E (cross-check): числа в тексте = числа в БД
- C (snapshot): для критичных JSON
- D (LLM-judge): для качественных утверждений

Подтвердить генерацию skeleton-файла? (yes/no): """


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("uc_id", help="UC ID (e.g. UC-A-04)")
    p.add_argument("--auto-confirm", action="store_true",
                    help="не спрашивать подтверждение (для автоматизации)")
    p.add_argument("--dry-run", action="store_true",
                    help="показать план, не писать файл")
    p.add_argument("--force", action="store_true",
                    help="переписать существующий файл")
    args = p.parse_args()

    uc = get_uc(args.uc_id)
    if uc is None:
        print(f"❌ {args.uc_id} не найден в USE_CASES.md §3 каталоге.")
        print("   Проверь ID или добавь UC в каталог сначала.")
        return 1

    if uc["confirmation"] != "confirmed":
        print(f"❌ {args.uc_id} имеет confirmation={uc['confirmation']!r}.")
        print("   Тест НЕ генерируется (UC-J-02 правило).")
        print("   Сначала подтверди UC: смени confirmation на 'confirmed' "
              "в USE_CASES.md §3.")
        return 2

    if uc["status"] == "speculative":
        print(f"❌ {args.uc_id} имеет status=speculative.")
        print("   Тест НЕ генерируется — сначала переведи в intended/partial/implemented.")
        return 2

    target = target_path(args.uc_id, uc)
    if target.exists() and not args.force:
        print(f"⚠️  Файл уже существует: {target.relative_to(SCRIPT_DIR)}")
        print("   Используй --force для перезаписи или удали вручную.")
        return 3

    plan = format_plan(args.uc_id, uc)

    if args.dry_run:
        print(plan)
        print("(dry-run — файл не пишется)")
        return 0

    if not args.auto_confirm:
        print(plan, end="")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"yes", "y", "да"}:
            print("Отменено.")
            return 0

    skel = build_skeleton(args.uc_id, uc)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skel, encoding="utf-8")
    print(f"✅ Skeleton создан: {target.relative_to(SCRIPT_DIR)}")
    print("   Дальше — заполни тестами по плану из tests/plans/{}.md".format(args.uc_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
