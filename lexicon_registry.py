#!/usr/bin/env python3.11
"""lexicon_registry.py — датчик §9: лексикон-в-коде (первый камень Потока F).

Ловит РОВНО класс, которым я сам обошёл §9 (RED_FLAG_TERMS в коде): модульная константа-
коллекция строковых литералов с ИМЕНЕМ-лексиконом (`*_TERMS/_MARKERS/_FLAGS/_LEXICON/_KEYWORDS`).
НЕ судит float-пороги (там семантика синтаксисом неотличима → FP-взрыв + §4). Пороги — реестр+аудит.

Ратчет (сиблинг producer_registry): любой найденный лексикон обязан быть в REGISTERED —
  db-backed (есть write-through-контур get_lexicon/seed → разрешено),
  structural (лингвистика/представление, не данные пациента → разрешено),
  legacy (терпим, но счёт заморожен — рост count → блок; census: не должен расти незаметно).
Незарегистрированный лексикон → находка. Census-симметрия: ключ реестра без реальной
константы → реестр протух.

Одна чистая функция audit_lexicons(scanned) — тестируется инъекцией. IO в _scan_lexicons.
Потребитель: pre-commit (чистый статик .py, БД НЕ нужна). Тесты: tests/unit/test_lexicon_registry.py.
Fail-closed: краш датчика = блок (чинить кодом, не флагом).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# Имя-сигнатура лексикона: суффикс, по которому распознаём доменный список терминов.
_LEXICON_NAME = re.compile(r".*(_TERMS|_MARKERS|_FLAGS|_LEXICON|_KEYWORDS)$")

# ── Реестр: qualname → вердикт. verdict: db-backed | structural | legacy ──────
# db-backed: есть контур БД (get_lexicon/seed), код-литерал только fallback (§9 ок).
# structural: лингвистика/форма, не данные пациента (§9 «остаётся в коде»).
# legacy: незамигрированный хардкод; count заморожен, рост = блок (мигрируй, не расти).
REGISTERED: dict[str, dict] = {
    # ── db-backed: контур БД есть (memory_config.get_lexicon + seed), литерал = fallback ──
    "memory_salience.SYMPTOM_TERMS": {
        "verdict": "db-backed", "oracle": "владелец",
        "note": "memory_lexicon.symptom_terms в system_config; код = fallback (07-07)"},
    "memory_salience.RED_FLAG_TERMS": {
        "verdict": "db-backed", "oracle": "владелец (врачебное)",
        "note": "memory_lexicon.red_flag_terms в system_config; код = fallback (07-07)"},
    "memory_salience.DURABLE_FACT_MARKERS": {
        "verdict": "db-backed", "oracle": "владелец",
        "note": "memory_lexicon.durable_fact_markers в system_config; код = fallback (07-07)"},
    # ── structural: лингвистика/инфра/представление, не данные пациента (§9 «остаётся в коде») ──
    "memory_salience._NEG_MARKERS": {
        "verdict": "structural", "oracle": "инженер",
        "note": "маркеры отрицания (нет/без/no) — лингвистика, не клинический список"},
    "cpic_reference_db._ADJUST_MARKERS": {
        "verdict": "structural", "oracle": "инженер",
        "note": "маркеры коррекции дозы в английском тексте рекомендаций CPIC — разбор формата документа, не данные пациента"},
    "hai_context._FULL_CONTEXT_KEYWORDS": {
        "verdict": "structural", "oracle": "инженер",
        "note": "триггеры полного контекста — системное поведение, не данные пациента"},
    "secrets_paths.DEV_CLONE_MARKERS": {
        "verdict": "structural", "oracle": "инженер",
        "note": "маркеры дев/стейджинг-клонов — инфра-эвристика об именах каталогов, "
                "не данные пациента. Дом переехал из integrity_tests (2026-08-12): "
                "рядом с понятием «владелец», потому что копия правила в другом модуле "
                "и была причиной того, что клон канона принимался за чужого тенанта"},
    "loinc_match._NEWBORN_SYSTEM_MARKERS": {
        "verdict": "structural", "oracle": "инженер",
        "note": "коды МАТЕРИАЛА самого LOINC (^BldCo, ^Fetus) — форма чужого "
                "справочника, а не факт о пациенте. Факт («взрослый») живёт в "
                "patient_profile и приходит сюда аргументом; в коде остаётся "
                "только знание о том, как LOINC кодирует пуповинную кровь"},
    # ── borderline: МОЙ вердикт structural (generic event-vocab, не клиника/идентичность),
    #    но на ревью владельца — если хочешь тюнить сам, переведём в db-backed. ──
    "calendar_client.TRAVEL_KEYWORDS": {
        "verdict": "structural", "oracle": "владелец? (борд­ерлайн)",
        "note": "generic travel-vocab для типа события; не клиника/идентичность. REVIEW"},
    "calendar_client.CHECKIN_KEYWORDS": {
        "verdict": "structural", "oracle": "владелец? (борд­ерлайн)",
        "note": "generic checkin-vocab. REVIEW"},
    "calendar_client.FLIGHT_KEYWORDS": {
        "verdict": "structural", "oracle": "владелец? (борд­ерлайн)",
        "note": "generic flight-vocab. REVIEW"},
}


def _module_name(py_path: Path, root: Path) -> str:
    rel = py_path.relative_to(root).with_suffix("")
    return ".".join(rel.parts)


def _is_str_collection(node: ast.AST) -> int | None:
    """Если node — Tuple/List/Set только из строковых констант, вернуть их число, иначе None."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    elts = node.elts
    if not elts:
        return None
    if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
        return len(elts)
    return None


def _scan_lexicons(root: Path) -> dict[str, dict]:
    """IO: обход .py под root, поиск МОДУЛЬНЫХ константа-лексиконов (имя-суффикс + коллекция
    строк). Возвращает qualname → {count, file, line}. Отделено ради тестируемости audit."""
    found: dict[str, dict] = {}
    skip = ("tests/", "docs/", "/archive", "/_archive", "node_modules/", "/.")
    for py in sorted(root.rglob("*.py")):
        rel = str(py.relative_to(root))
        if any(s in ("/" + rel) or rel.startswith(s) for s in skip):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in tree.body:  # только МОДУЛЬНЫЙ уровень
            # Assign (X = (...)) И AnnAssign (X: tuple[str,...] = (...)) — иначе пропустим
            # аннотированные константы (SYMPTOM_TERMS: tuple = ...), а это главная цель.
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            n = _is_str_collection(value)
            if n is None:
                continue
            for tgt in targets:
                if isinstance(tgt, ast.Name) and _LEXICON_NAME.match(tgt.id):
                    found[f"{_module_name(py, root)}.{tgt.id}"] = {
                        "count": n, "file": rel, "line": node.lineno,
                    }
    return found


def audit_lexicons(scanned: dict[str, dict]) -> list[str]:
    """Ратчет §9. Пусто = каждый лексикон классифицирован и легаси не растёт.

    Находки: (1) лексикон не в REGISTERED → вынеси в БД или зарегистрируй вердикт;
    (2) legacy вырос (count > зафиксированного) → мигрируй, не расти;
    (3) ключ REGISTERED без реальной константы → реестр протух (census-симметрия).
    """
    found: list[str] = []
    unregistered = sorted(set(scanned) - set(REGISTERED))
    if unregistered:
        found.append(
            f"{len(unregistered)} лексикон(ов) в коде вне реестра §9: {', '.join(unregistered)}. "
            f"Вынеси в system_config (эталон memory_config: get_lexicon+seed+fallback) ИЛИ "
            f"зарегистрируй вердикт structural/legacy в lexicon_registry.REGISTERED. "
            f"См. docs/how-to/move_lexicon_to_db.md"
        )
    for q, meta in REGISTERED.items():
        if meta.get("verdict") == "legacy" and q in scanned:
            frozen = meta.get("count", 0)
            if scanned[q]["count"] > frozen:
                found.append(
                    f"legacy-лексикон {q} ВЫРОС ({frozen}→{scanned[q]['count']}): "
                    f"не расти хардкод, мигрируй в БД (§9)."
                )
    stale = sorted(set(REGISTERED) - set(scanned))
    if stale:
        found.append(
            f"реестр §9 протух — ключи без реальной константы: {', '.join(stale)} "
            f"(константу переименовали/убрали? почисти REGISTERED)."
        )
    return found


def collect_lexicon_findings(root: Path | None = None) -> list[str]:
    """Прод-вход (pre-commit): скан + аудит. Fail-safe: краш → находка (fail-closed)."""
    r = root or Path(__file__).resolve().parent
    try:
        return audit_lexicons(_scan_lexicons(r))
    except Exception as e:  # noqa: BLE001
        return [f"датчик §9 (lexicon_registry) сам упал: {type(e).__name__}: {e} — чини датчик."]


if __name__ == "__main__":
    import json
    import sys
    sc = _scan_lexicons(Path(__file__).resolve().parent)
    print(json.dumps(sc, ensure_ascii=False, indent=2))
    findings = audit_lexicons(sc)
    print("\n=== findings ===")
    for f in findings:
        print(" •", f)
    sys.exit(1 if findings else 0)
