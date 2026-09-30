#!/usr/bin/env python3.11
"""
validate_uc_index.py — UC-J-03: проверка целостности uc_index.yaml.

Запускается:
- через pre-commit (вместе с smoke_tests);
- ручным `python3.11 validate_uc_index.py`.

Проверяет:
1. Каждая запись имеет обязательные поля (status, confirmation, priority, testability).
2. Поля `tests` ссылаются на реально существующие файлы.
3. Поля `modules` ссылаются на реально существующие .py.
4. Каждый `confirmed` UC из USE_CASES.md §3 каталога имеет запись в uc_index.yaml
   (или явно помечен `tests: []`).

Exit code: 0 — OK; 1 — ошибки целостности.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from doc_translation import strip_switch

SCRIPT_DIR = Path(__file__).parent
INDEX_FILE = SCRIPT_DIR / "uc_index.yaml"
USE_CASES = SCRIPT_DIR / "USE_CASES.md"


REQUIRED_FIELDS = {"status", "confirmation", "priority", "testability"}
VALID_STATUS = {"implemented", "partial", "intended", "speculative"}
VALID_CONFIRMATION = {"proposed", "confirmed", "rejected"}


def _load_yaml_simple(path: Path) -> dict:
    """
    Минималистичный парсер уровня «один-уровень-вложенности» для нашего формата.
    Не пытаемся быть полным YAML — это снимает зависимость от PyYAML.
    """
    text = path.read_text(encoding="utf-8")
    result: dict = {"version": "", "ucs": {}}
    in_ucs = False
    cur_uc: dict | None = None
    cur_uc_id: str | None = None
    cur_list_field: str | None = None

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw.startswith("version:"):
            result["version"] = stripped.split(":", 1)[1].strip().strip('"')
            continue
        if raw.startswith("updated:"):
            result["updated"] = stripped.split(":", 1)[1].strip().strip('"')
            continue
        if raw.startswith("ucs:"):
            in_ucs = True
            continue
        if not in_ucs:
            continue

        # Уровень UC ID: «  UC-X-NN:» (2 пробела)
        m_uc = re.match(r"^  ([A-Z][A-Z0-9-]+):\s*$", raw)
        if m_uc:
            if cur_uc is not None and cur_uc_id is not None:
                result["ucs"][cur_uc_id] = cur_uc
            cur_uc_id = m_uc.group(1)
            cur_uc = {}
            cur_list_field = None
            continue

        # Поле уровня 4-пробела
        m_field = re.match(r"^    (\w+):\s*(.*)$", raw)
        if m_field and cur_uc is not None:
            key, val = m_field.groups()
            val = val.strip()
            cur_list_field = None
            if val == "":
                # Возможно начало списка
                cur_list_field = key
                cur_uc[key] = []
                continue
            if val.startswith("[") and val.endswith("]"):
                # Inline list
                inner = val[1:-1].strip()
                items = [x.strip().strip('"') for x in inner.split(",") if x.strip()]
                cur_uc[key] = items
                continue
            cur_uc[key] = val.strip('"')
            continue

        # Элемент списка: «      - item»
        m_li = re.match(r"^      - (.*)$", raw)
        if m_li and cur_uc is not None and cur_list_field:
            cur_uc[cur_list_field].append(m_li.group(1).strip().strip('"'))
            continue

    if cur_uc is not None and cur_uc_id is not None:
        result["ucs"][cur_uc_id] = cur_uc

    return result


def _confirmed_ucs_from_use_cases() -> set[str]:
    if not USE_CASES.exists():
        return set()
    text = strip_switch(USE_CASES.read_text(encoding="utf-8"))
    confirmed: set[str] = set()
    pattern = re.compile(
        r"\|\s*`(UC-[A-Z]-\d+|UC-X-\d+|UC-J-\d+|UC-K-\d+)`"
        r"[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*confirmed\s*\|"
    )
    for m in pattern.finditer(text):
        confirmed.add(m.group(1))
    return confirmed


def validate() -> tuple[int, list[str]]:
    errors: list[str] = []

    if not INDEX_FILE.exists():
        return 1, [f"❌ {INDEX_FILE.name} не существует"]

    data = _load_yaml_simple(INDEX_FILE)
    ucs = data.get("ucs", {})
    if not ucs:
        return 1, ["❌ uc_index.yaml пуст или не парсится"]

    for uc_id, rec in ucs.items():
        # Required fields
        missing = REQUIRED_FIELDS - set(rec.keys())
        if missing:
            errors.append(f"{uc_id}: missing fields {missing}")

        # Valid values
        if rec.get("status") not in VALID_STATUS:
            errors.append(f"{uc_id}: invalid status={rec.get('status')!r}")
        if rec.get("confirmation") not in VALID_CONFIRMATION:
            errors.append(f"{uc_id}: invalid confirmation={rec.get('confirmation')!r}")

        # tests/ files must exist (если указаны)
        for t in rec.get("tests", []):
            tp = SCRIPT_DIR / t
            if not tp.exists():
                errors.append(f"{uc_id}: test file missing: {t}")

        # modules — должны существовать. Для partial/intended допускаем
        # будущие модули (lab_extractor.py, propose_uc.py до реализации).
        for m in rec.get("modules", []):
            mp = SCRIPT_DIR / m
            if not mp.exists():
                if rec.get("status") in ("intended", "speculative", "partial"):
                    # warning не error — пишем в notes
                    pass
                else:
                    errors.append(f"{uc_id}: module missing: {m}")

    # Coverage: все confirmed UC из USE_CASES.md должны иметь запись
    confirmed_in_md = _confirmed_ucs_from_use_cases()
    confirmed_in_index = set(ucs.keys())
    missing_in_index = confirmed_in_md - confirmed_in_index
    if missing_in_index:
        errors.append(
            f"Confirmed в USE_CASES.md, но НЕТ в uc_index.yaml: "
            f"{sorted(missing_in_index)}"
        )

    return (1 if errors else 0), errors


def main() -> int:
    code, errors = validate()
    if errors:
        print("❌ uc_index.yaml validation FAILED:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("✅ uc_index.yaml OK")
    return code


if __name__ == "__main__":
    sys.exit(main())
