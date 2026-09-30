"""cbcr_lookup.py — tool для чтения CBCR wiki из hypothesis generator.

Wave 5A W-2 (2026-05-13).

Используется как Anthropic tool в hai_hypotheses.generate_hypothesis_from_*.
LLM сама решает когда нужна детализация методологии — вызывает
read_cbcr_concept(name).

Wiki: ~/iCloud/health/methodology/cbcr/wiki/ — 96 концептов с YAML
frontmatter (aliases:). Alias resolver разворачивает синонимы.

Public API:
    read_cbcr_concept(name) -> str
    get_tool_schema() -> dict (для Anthropic tools= параметра)

Cache: LRU per-process (концепты read-only, не меняются между вызовами
в одной сессии генератора).
"""
from __future__ import annotations

import errno
import re
import time as _time_mod
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent / "methodology" / "cbcr" / "wiki"  # git (СК-1: load-bearing не в iCloud, 2026-07-11)
SUGGESTIONS_TOP_K = 5

# iCloud (CloudKit) под конкурентным чтением (full pytest -n auto, или несколько
# generate_hypothesis параллельно) возвращает OSError errno=EAGAIN [11]
# "Resource deadlock avoided". Python read_text не retry'ит — это наша задача.
# Урок 2026-06-29: 3 fail в test_cbcr_lookup в ночном suite, solo run 11/11 pass.
# Total max delay = 50+100+200 = 350ms — потом raise.
_EAGAIN_RETRY_DELAYS_SEC = (0.05, 0.10, 0.20)


def _read_text_with_retry(path: Path, *, encoding: str = "utf-8") -> str:
    """read_text с retry на EAGAIN от iCloud/CloudKit. См. _EAGAIN_RETRY_DELAYS_SEC."""
    last_err: OSError | None = None
    for delay in (0.0, *_EAGAIN_RETRY_DELAYS_SEC):
        if delay:
            _time_mod.sleep(delay)
        try:
            return path.read_text(encoding=encoding)
        except OSError as e:
            if e.errno != errno.EAGAIN:
                raise
            last_err = e
    # Все retry исчерпаны — отдаём последнюю ошибку.
    assert last_err is not None
    raise last_err


def _strip_frontmatter(text: str) -> str:
    """Удаляет YAML frontmatter (--- ... ---) из начала markdown."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5:].lstrip()


def _parse_frontmatter(text: str) -> dict:
    """Минимальный YAML parser для frontmatter — только aliases: list."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fm_text = text[4:end]
    aliases: list[str] = []
    in_aliases = False
    for line in fm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("aliases:"):
            in_aliases = True
            continue
        if in_aliases:
            if stripped.startswith("- "):
                alias = stripped[2:].strip().strip("'\"")
                if alias:
                    aliases.append(alias)
            elif stripped and not stripped.startswith("-"):
                in_aliases = False
    return {"aliases": aliases}


@lru_cache(maxsize=1)
def _build_alias_index() -> dict[str, Path]:
    """Скан всех wiki/*.md → {alias_lower: Path}.

    Канонические имена (filename без .md) и aliases из frontmatter — все
    мапятся на путь к файлу. lru_cache, потому что wiki не меняется в сессии.
    """
    index: dict[str, Path] = {}
    if not WIKI_DIR.exists():
        return index
    for f in WIKI_DIR.glob("*.md"):
        canonical = f.stem
        index[canonical.lower()] = f
        try:
            meta = _parse_frontmatter(_read_text_with_retry(f))
        except Exception:  # silent-ok: damaged file skipped, не блокер
            continue
        for alias in meta.get("aliases", []):
            index.setdefault(alias.lower(), f)
    return index


def read_cbcr_concept(name: str) -> str:
    """Читает CBCR wiki статью по канонич. имени или alias.

    Возвращает body статьи без YAML-frontmatter.
    Если concept не найден — текст с top-K suggestions через get_close_matches.
    """
    if not WIKI_DIR.exists():
        return (
            f"❌ CBCR wiki недоступна. Ожидаемый путь: {WIKI_DIR}\n"
            "Файлы в git (methodology/cbcr/wiki) — проверь рабочее дерево."
        )

    index = _build_alias_index()
    key = name.strip().lower()
    path = index.get(key)
    if path is None:
        keys = sorted(index.keys())
        suggestions = get_close_matches(key, keys, n=SUGGESTIONS_TOP_K, cutoff=0.4)
        if suggestions:
            sug_text = "\n".join(f"  - {s}" for s in suggestions)
            return (
                f"❌ Концепт '{name}' не найден.\n\n"
                f"Близкие совпадения (попробуй один из них):\n{sug_text}\n\n"
                f"Также используй канонические имена из manifest §«Углубление методологии»."
            )
        return (
            f"❌ Концепт '{name}' не найден и нет близких совпадений.\n"
            "Возможно ошибка в имени. Канонические имена см. в cbcr_manifest.md §«Углубление»."
        )

    try:
        content = _read_text_with_retry(path)
    except Exception as e:
        return f"❌ Ошибка чтения {path.name}: {e}"

    body = _strip_frontmatter(content)
    # Лёгкая очистка: убрать броские wikilinks-маркеры вроде "ch03_05_..." из body —
    # они не информативны для LLM-критика, только шум. Сами [[wikilinks]] оставляем.
    body = re.sub(r"\bch\d{2}_\d{2}_[a-z_]+\b", "", body)
    return body.strip()


def get_tool_schema() -> dict:
    """JSON Schema для Anthropic tools= параметра.

    Используй: client.messages.create(..., tools=[get_tool_schema()], ...).
    """
    return {
        "name": "read_cbcr_concept",
        "description": (
            "Прочитать статью из CBCR wiki — методологии клинического "
            "рассуждения (ten Cate 2018). Используй когда нужна детализация "
            "методологии для текущей гипотезы. Например: 'Illness Script' для "
            "структуры памяти, 'Semantic Qualifiers' для языка формулировки, "
            "'Bias' для типов когнитивных биasов. Поддерживает aliases — можно "
            "писать любой синоним из статьи. Возвращает body статьи на английском."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Имя концепта или alias. Канонические имена: "
                        "'Illness Script', 'Semantic Qualifiers', 'Differential Diagnosis', "
                        "'Hypothesis-Driven Inquiry', 'Dual Process Theory', 'Bias', "
                        "'Availability Bias', 'Representative Bias', 'Problem Representation', "
                        "'Encapsulated Knowledge', 'Contrastive Learning', 'Fault'."
                    ),
                }
            },
            "required": ["name"],
        },
    }


if __name__ == "__main__":
    # CLI smoke test
    import sys

    if len(sys.argv) < 2:
        print(f"Wiki path: {WIKI_DIR}")
        print(f"Wiki exists: {WIKI_DIR.exists()}")
        index = _build_alias_index()
        print(f"Concepts indexed: {len(set(index.values()))}")
        print(f"Alias entries: {len(index)}")
        print("\nUsage: python3 cbcr_lookup.py 'concept name'")
        sys.exit(0)
    name = " ".join(sys.argv[1:])
    print(f"=== read_cbcr_concept({name!r}) ===\n")
    print(read_cbcr_concept(name))
