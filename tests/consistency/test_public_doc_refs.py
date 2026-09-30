"""Публичный документ не ведёт читателя в закрытую часть и в пустоту (27.09, нить export-refs).

В открытой выгрузке закрытой зоны нет: гиперссылка туда — битая ссылка у первого же читателя,
упоминание рабочего плана без пометки — обещание файла, которого у него не будет. Замер 27.09:
9 битых гиперссылок и 13 упоминаний плана/журнала нитей без пометки. Правило:
- гиперссылка на закрытый или несуществующий файл запрещена;
- упоминание закрытого пути разрешено, только если на той же строке сказано «закрытой части»;
- имена журналов (BACKLOG, CHANGELOG, ROADMAP, SECURITY_AUDIT_LOG) — соглашение, их можно
  называть без пометки, но не ссылаться на них;
- пути, которые установка приносит сама (private/, launchd/, data/norm_docs/, clinical_kb/),
  упоминать можно — это инструкция установки.
CLAUDE.md исключён до решения владельца, публикуется ли он вообще.
"""
import fnmatch
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

MARK = "закрытой части"
MARKS = (MARK, "private part")  # пометка в оригинале и в английском переводе
JOURNALS = {"BACKLOG.md", "BACKLOG_ARCHIVE.md", "CHANGELOG.md", "ROADMAP.md", "SECURITY_AUDIT_LOG.md",
            "CLAUDE.md"}   # 28.09: свод правил в закрытой части; голое упоминание свода без пометки допустимо, ссылка — нет
INSTALL = ("private/*", "launchd/*", "data/norm_docs/*", "methodology/clinical_kb/*")
EXEMPT: dict[str, str] = {}   # CLAUDE.md ушёл в закрытую часть 28.09 — исключений нет
_TOKEN = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*[\w.-]+\.(?:md|py|yaml|yml|json|sh|txt|conf|xlsx))(?![\w/-])')
_LINK = re.compile(r'\]\(([^)\s]+)\)')


def problems(rel, text, root, is_private):
    out = []
    base = pathlib.PurePosixPath(rel).parent
    for i, line in enumerate(text.split("\n"), 1):
        for target in _LINK.findall(line):
            t = target.split("#", 1)[0]
            if not t or "<" in t or re.match(r"[a-z]+:", t):
                continue
            norm = pathlib.Path(str(root / base / t)).resolve()
            try:
                tr = str(norm.relative_to(root.resolve()))
            except ValueError:
                continue
            if is_private(tr):
                out.append(f"{rel}:{i}: ссылка в закрытую часть → {tr}")
            elif not norm.exists():
                out.append(f"{rel}:{i}: ссылка на несуществующий файл → {tr}")
        if any(m in line for m in MARKS):
            continue
        for tok in _TOKEN.findall(line):
            if (tok not in JOURNALS and is_private(tok)
                    and not any(fnmatch.fnmatch(tok, g) for g in INSTALL)
                    and (root / tok).exists()):
                out.append(f"{rel}:{i}: закрытый путь без пометки «{MARK}» → {tok}")
    return out


def _zone():
    import pii_census as pc
    root = pc.ROOT
    globs = pc._zones(root)
    tracked = pc._tracked(root)
    return root, tracked, (lambda p: pc._is_private(p, globs))


def test_public_docs_have_no_private_or_broken_refs():
    root, tracked, is_private = _zone()
    found = []
    for rel in sorted(p for p in tracked if p.endswith(".md") and not is_private(p)):
        if rel in EXEMPT or not (root / rel).exists():
            continue
        found += problems(rel, (root / rel).read_text(errors="ignore"), root, is_private)
    assert not found, "\n".join(found)


def test_detector_sees_planted_refs():
    """Негативный контроль: подложенные битая ссылка, ссылка на журнал и немой план — пойманы."""
    root, _, is_private = _zone()
    text = ("[x](../nope_missing.md)\n[b](../BACKLOG.md)\nсм. `plans/PLAN_x.md`\n"
            "см. `plans/PLAN_x.md` (в закрытой части проекта)\nжурнал BACKLOG.md\n")
    got = problems("docs/x.md", text, root, is_private)
    assert [g.split(": ", 1)[0] for g in got][:2] == ["docs/x.md:1", "docs/x.md:2"]
    assert not any(g.startswith(("docs/x.md:4", "docs/x.md:5")) for g in got)
    import pii_census as pc
    if pc.is_public_export():
        return  # закрытой зоны на диске нет — немое упоминание проверить не на чем
    plan = next(p for p in sorted(pc._tracked(root)) if p.startswith("plans/") and p.endswith(".md"))
    got = problems("docs/x.md", f"см. `{plan}`\nсм. `{plan}` ({MARK})\nsee `{plan}` (private part)\n", root, is_private)
    assert [g.split(": ", 1)[0] for g in got] == ["docs/x.md:1"]
