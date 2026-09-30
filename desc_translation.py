"""Память машинного перевода пояснений для английских справочников (нить arch-en-gen, 29.09).

Генераторы ARCH_SNAPSHOT и TESTING_CONTRACTS несут пояснения из данных — первые строки
докстрингов, комментарии к константам, причины xfail. Дом этих текстов — код, и он русский.
Решение владельца 29.09: английская версия справочника берёт перевод из ПАМЯТИ, а не пишет
второе пояснение руками. Ключ памяти — хеш русского текста: поменялся оригинал — ключ другой,
запись пропала из попаданий, и пояснение снова уходит на перевод. Второго дома текста нет:
память — производное, её можно стереть и пересобрать.

Формат `methodology/i18n/desc_memory.en.yaml`: {<sha12 русского>: {ru: <текст>, en: <перевод>}}.
`ru` хранится ради ревью глазами и сборки мусора, не как второй источник.

Граница честно: механически проверяется форма перевода (нет кириллицы, те же `код`,
{плейсхолдеры} и числа), не смысл. Смысл — выборочно глазами (план нити, ворота 2).
"""
import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
MEMORY = ROOT / "methodology" / "i18n" / "desc_memory.en.yaml"
_BATCH = 40
_CYR = re.compile(r"[А-Яа-яЁё]")
_CODE = re.compile(r"`[^`]+`")
_PLACE = re.compile(r"\{[^{}]*\}")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")

_PROMPT = (
    "Translate each Russian line below into concise, plain technical English. These are short "
    "descriptions of functions, checks and constants in a software project. Keep every `code` "
    "span, {placeholder}, number, identifier and file name exactly as written. Do not add or "
    "drop information, do not explain. Answer with a JSON array of strings only — one "
    "translation per input line, in the same order, the same number of items.\n\n")


def memory_key(ru: str) -> str:
    return hashlib.sha256(ru.strip().encode("utf-8")).hexdigest()[:12]


def load_memory(path: Path = MEMORY) -> dict:
    if not path.exists():
        return {}
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


_CACHE: dict = {}


def _cached_memory(path: Path = MEMORY) -> dict:
    """Память читается один раз на изменение файла: генератор зовёт english() сотни раз."""
    stamp = path.stat().st_mtime_ns if path.exists() else None
    if _CACHE.get("stamp") != stamp or _CACHE.get("path") != path:
        _CACHE.update(stamp=stamp, path=path, memory=load_memory(path))
    return _CACHE["memory"]


def english(ru: str, memory: dict | None = None) -> str | None:
    """Перевод пояснения из памяти; нет записи — None (генератор помечает, датчик считает)."""
    if not _CYR.search(ru):
        return ru
    rec = (memory if memory is not None else _cached_memory()).get(memory_key(ru))
    return rec["en"] if rec else None


# Генераторы английских справочников: у каждого <имя>_descriptions() отдаёт русские пояснения
# ровно в том виде, в каком кладёт их в блок (после своей обрезки) — это и есть ключи памяти.
_GENERATORS = {"gen_blueprint": "blueprint_descriptions", "arch_guard": "arch_graph_descriptions",
               "gen_key_paths": "key_paths_descriptions", "gen_schedule": "schedule_descriptions",
               "gen_arch_blocks": "arch_blocks_descriptions",
               "gen_testing_contracts": "testing_contracts_descriptions"}


def collect_all_descriptions() -> list[str]:
    import importlib
    out: list[str] = []
    for module, func in _GENERATORS.items():
        out += getattr(importlib.import_module(module), func)()
    return out


def desc_problems(ru: str, en) -> list[str]:
    """Что в переводе не так по форме. Пустой список — форма цела (смысл не судится)."""
    if not isinstance(en, str) or not en.strip():
        return ["empty"]
    out = []
    if _CYR.search(en):
        out.append("cyrillic")
    for name, rx in (("code", _CODE), ("placeholders", _PLACE), ("numbers", _NUM)):
        if sorted(rx.findall(ru)) != sorted(rx.findall(en)):
            out.append(name)
    return out


def _translate_batch(lines: list[str]) -> list[str]:
    import llm_client
    import hai_core
    body = _PROMPT + "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
    resp = llm_client.guarded_client().messages.create(
        model=hai_core.get_model("sonnet"), max_tokens=8000,
        messages=[{"role": "user", "content": body}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    text = text[text.find("["): text.rfind("]") + 1]
    out = json.loads(text)
    if not isinstance(out, list) or len(out) != len(lines):
        raise ValueError(f"batch answer has {len(out) if isinstance(out, list) else '?'} items, want {len(lines)}")
    return out


def refresh(texts, *, translate=_translate_batch, path: Path = MEMORY,
            prune: bool = True, dry_run: bool = False) -> dict:
    """Довести память до набора русских пояснений `texts`.

    Недостающие переводятся партиями; перевод с нарушенной формой не сохраняется (остаётся
    «без перевода» — его видит датчик). prune=True выбрасывает записи, чьих оригиналов в
    наборе больше нет. Возвращает отчёт: added, rejected, pruned, missing.
    """
    wanted = {memory_key(t): t.strip() for t in texts if _CYR.search(t)}
    memory = load_memory(path)
    todo = [t for k, t in wanted.items() if k not in memory]
    report = {"added": 0, "rejected": [], "pruned": 0, "missing": 0}
    for i in range(0, len(todo), _BATCH):
        part = todo[i:i + _BATCH]
        try:
            answers = translate(part)
        except Exception as e:  # партия не роняет остальные; её строки остаются без перевода
            log.error("desc_translation: партия %d не переведена: %s", i // _BATCH, e)
            report["rejected"] += [(t, "batch:" + type(e).__name__) for t in part]
            continue
        for ru, en in zip(part, answers):
            bad = desc_problems(ru, en)
            if bad:
                report["rejected"].append((ru, ",".join(bad)))
            else:
                memory[memory_key(ru)] = {"ru": ru, "en": en.strip()}
                report["added"] += 1
    if prune:
        stale = [k for k in memory if k not in wanted]
        for k in stale:
            del memory[k]
        report["pruned"] = len(stale)
    report["missing"] = sum(1 for k in wanted if k not in memory)
    if not dry_run:
        import yaml
        path.write_text(yaml.safe_dump(dict(sorted(memory.items())), allow_unicode=True,
                                       sort_keys=False, width=10**6), encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        # Пополнение памяти на Studio (генераторы Studio-only): зовёт scripts/arch_regen.sh.
        rep = refresh(collect_all_descriptions())
        print(f"added {rep['added']} rejected {len(rep['rejected'])} pruned {rep['pruned']} missing {rep['missing']}")
        for ru, why in rep["rejected"][:20]:
            print(f"  rejected [{why}]: {ru[:80]}")
        sys.exit(0)
    # Самопроверка без модели и без файлов проекта.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.yaml"
        fake = lambda part: ["Returns `x` for {n} rows, 3 times" if "`x`" in t else "Привет" for t in part]
        r = refresh(["Отдаёт `x` для {n} строк, 3 раза", "Плохой перевод"], translate=fake, path=p)
        assert r["added"] == 1 and len(r["rejected"]) == 1 and r["missing"] == 1, r
        assert english("Отдаёт `x` для {n} строк, 3 раза", load_memory(p)) == "Returns `x` for {n} rows, 3 times"
        r = refresh(["Другое"], translate=lambda part: ["Other"], path=p)
        assert r["pruned"] == 1 and r["added"] == 1, r
    print("desc_translation self-check: ok")
