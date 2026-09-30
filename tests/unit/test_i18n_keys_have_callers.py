"""Каждая строка словаря кому-то нужна (29.09).

Словарь methodology/i18n — дом текстов человеку. Коммит f06e5b7 (28.09) перевёл сбои в журнал,
и 30 прежних текстов ошибок осиротели: их никто не вызывал, но их вычитывали, переводили и
выносили владельцу на решение (таблица понятности, строка labs.error.pending_review_not_found).
Сторож: у ключа есть вызывающий — литерал ключа в коде, либо его пространство имён собирается
динамически (f"ns.{x}", "ns." + x) или передаётся целиком (fmt_label(v, "ns")).
Граница: ключ, собранный из кусков иначе (f"{a}.{b}"), сторож не видит — он честно
покраснеет, и такой ключ надо объявить в DYNAMIC ниже с причиной."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DYNAMIC: dict[str, str] = {}   # ключ → где и как он собирается


def _code() -> str:
    parts = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if p.suffix in (".py", ".html") and not rel.startswith(("tests/", ".git/", ".venv/")) \
                and "/site-packages/" not in rel:
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def orphans(keys, code: str) -> list[str]:
    spaces = {k.rsplit(".", 1)[0] for k in keys} | {".".join(k.split(".")[:2]) for k in keys}
    literals = set(re.findall(r"[\"']([a-z_]+(?:\.[a-z0-9_]+)+)[\"']", code))
    prefixes = set(re.findall(r"f?[\"']([a-z_]+(?:\.[a-z0-9_]+)*\.)(?:\{|[\"']\s*\+)", code))
    # «"ns.key" + суффикс» (safety.what_to_do + "_critical"): литерал перед «+» — префикс ключей
    prefixes |= set(re.findall(r"[\"']([a-z_]+(?:\.[a-z0-9_]+)+)[\"']\s*\+", code))
    prefixes |= {lit + "." for lit in literals if lit in spaces}
    return sorted(k for k in keys if k not in literals and k not in DYNAMIC
                  and not any(k.startswith(p) for p in prefixes))


def test_every_dictionary_key_has_a_caller():
    keys = yaml.safe_load((ROOT / "methodology/i18n/ru.yaml").read_text(encoding="utf-8"))
    dead = orphans(keys, _code())
    assert not dead, f"ключи без вызывающего (удали из ru и en или объяви в DYNAMIC): {dead}"


def test_detector_sees_literals_prefixes_and_namespaces():
    keys = ["a.b.one", "a.b.two", "c.d.three", "e.f.four", "g.h.dead"]
    code = 't("a.b.one")\nt(f"c.d.{x}")\nfmt_label(v, "e.f")\n'
    assert orphans(keys, code) == ["a.b.two", "g.h.dead"]
    # сборка «ключ + суффикс» (как safety.what_to_do + "_critical") — не сирота
    assert orphans(["s.w", "s.w_critical"], 't("s.w" + crit)') == []
