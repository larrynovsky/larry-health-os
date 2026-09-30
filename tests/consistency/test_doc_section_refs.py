"""Сторож ссылок на разделы свода и карты: указатель не должен пережить предмет.

Класс, который лечим (CLAUDE.md §18 — утверждение вне своего носителя):
ссылка «CLAUDE.md §14» или «BLUEPRINT §23» живёт в ЧУЖОМ файле, а раздел
удаляют или переименовывают в своём. Гасителя у такой ссылки нет ни одного,
и она молча начинает указывать в пустоту.

Три находки 2026-08-02, породившие сторож:
  · tests/charters/CH-PRIMARY-01.md → «BLUEPRINT §«ПРАВИЛО БЭКАПОВ»» — раздел
    удалён в тот же день той же сессией, которая ссылку и не заметила;
  · тот же чартер → «BLUEPRINT.md §single-primary» — раздела с таким именем
    не существовало никогда;
  · triage_agent.py + test_uc_d_04_triage.py → «BLUEPRINT §23», норма из
    которого переехала в свод.

ИМЕНОВАННЫЕ ссылки («§«ПРАВИЛО БЭКАПОВ»», «§single-primary») запрещены целиком,
а не проверяются нечётким матчером: заголовок — это редакция, он переписывается;
номер — якорь, он в CLAUDE.md объявлен неизменным. Запрет дешевле матчера и
закрывает ровно тот класс, который умер.

История (CHANGELOG, архивы, handoff, планы) вправе называть мёртвое — она о
прошлом. Поэтому эти пути вне периметра сторожа.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# История о прошлом; называть в ней мёртвые разделы законно.
SKIP_PARTS = ("CHANGELOG.md", "BACKLOG_ARCHIVE.md", "SECURITY_AUDIT_LOG.md",
              "docs/handoff/", "plans/", "tests/plans/",
              # План-аудит: цитирует структуру доков на момент составления
              # (со ссылками на конкретные СТРОКИ), это снимок, а не указатель.
              "docs/DIATAXIS_WORKPLAN")

# «CLAUDE.md §15», «BLUEPRINT §3.14», «Blueprint §23», «BLUEPRINT.md §«X»»
_REF = re.compile(r"\b(CLAUDE|BLUEPRINT)(?:\.md)?\s*§+\s*([^\s,;:)\]}]+)", re.I)
# Разделов не больше двух цифр (в BLUEPRINT максимум §25, в своде §20).
# Трёхзначное после § — это bug-id реестра инвариантов («BLUEPRINT §253»),
# у него свой сторож: tests/consistency/test_intent_ref_exists.py.
_NUM = re.compile(r"^\d{1,2}(\.\d+[a-zа-я]?)?$")
_BUG_ID = re.compile(r"^\d{3,}$")
_TRIM = ".`\"'»)]*_"


def _tokens(raw: str) -> list[str]:
    """«8/§10» → ['8','10']; «3-5» → ['3','5']; «15\"» → ['15']."""
    out = []
    for part in re.split(r"[/,]", raw.replace("§", "")):
        part = part.strip(_TRIM)
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        out.extend([m.group(1), m.group(2)] if m else [part])
    return out


def _tracked() -> list[Path]:
    """Отслеживаемые И ещё не добавленные файлы.

    `--others` добавлен 2026-08-02 после осечки: новая страница
    `docs/reference/data_ingestion_paths.md` со ссылкой на снятый раздел прошла
    мимо сторожа, потому что на момент прогона была untracked, — и покраснела
    уже на Studio, после коммита. Сторож, judging только индекс, проверяет файл
    на один коммит позже, чем нужно; это тот же класс, что «переименование не
    считается новым файлом» у dispgate.

    Спрашиваем через `git_facts` (2026-08-10): в песочнице `test_on_studio.sh`
    репозитория нет по построению, и прямой `git ls-files` падал там rc=128 —
    сторож краснел от СРЕДЫ, а не от кода, пять раз за прогон. §20 наизнанку.
    """
    import git_facts
    files = []
    for rel in git_facts.tracked("*.py", "*.md", "*.yaml", "*.json"):
        if any(s in rel for s in SKIP_PARTS):
            continue
        files.append(ROOT / rel)
    return files


def _claude_sections() -> set[str]:
    """§-ID свода + его ИМЕНОВАННЫЕ якоря.

    У свода есть устойчивые нечисловые разделы (A/B/C/D/E, «§ Read-first»,
    «§ Квитанция ponytail») — на них ссылаться законно, они объявлены
    структурой файла, а не редакцией заголовка.
    """
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    known = set(re.findall(r"\*\*§(\d+)\b", text))
    known |= set(re.findall(r"^##\s+([A-EА-Я])\.", text, re.M))
    for head in re.findall(r"^##\s+§\s*(.+)$", text, re.M):
        known |= {w.strip(_TRIM) for w in head.split() if len(w) > 2}
    known |= {"Среда", "Read-first"}   # подзаголовки внутри C и B
    return known


def _blueprint_sections() -> set[str]:
    """Пусто: BLUEPRINT.md удалён 2026-08-03 — разделов нет и файла нет.

    Читается с диска, а не вписано константой (§18): если файл вернётся с
    разделами, ссылки на них перестанут считаться мёртвыми сами, без правки здесь.
    """
    bp = ROOT / "BLUEPRINT.md"
    if not bp.exists():
        return set()
    return set(re.findall(r"^#{2,3}\s+(\d+(?:\.\d+[a-zа-я]?)?)[.\s]",
                          bp.read_text(encoding="utf-8"), re.M))


def _collect() -> tuple[list[str], list[str]]:
    """→ (мёртвые числовые ссылки, именованные ссылки)."""
    claude, blueprint = _claude_sections(), _blueprint_sections()
    dead, named = [], []
    for path in _tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.name == "test_doc_section_refs.py":
            continue  # сторож цитирует мёртвые ссылки как материал
        for lineno, line in enumerate(text.splitlines(), 1):
            for doc, raw in _REF.findall(line):
                where = f"{path.relative_to(ROOT)}:{lineno}"
                is_claude = doc.upper() == "CLAUDE"
                known = claude if is_claude else blueprint
                for sec in _tokens(raw):
                    if _BUG_ID.match(sec):
                        continue        # bug-id реестра, не раздел
                    if not _NUM.match(sec):
                        # У свода именованные якоря устойчивы, у карты — нет.
                        if is_claude and sec in known:
                            continue
                        named.append(f"{where} → {doc} §{sec}")
                        continue
                    # §3.14 живёт под заголовком «### 3.14», §3 — под «## 3.»
                    if sec not in known and sec.split(".")[0] not in known:
                        dead.append(f"{where} → {doc} §{sec}")
    return dead, named


def test_no_dead_section_refs():
    dead, _ = _collect()
    assert not dead, (
        "Ссылка на несуществующий раздел (§18: указатель пережил предмет):\n  "
        + "\n  ".join(dead)
        + "\nЛибо раздел вернуть, либо ссылку перенаправить в новый дом."
    )


def test_no_named_section_refs():
    _, named = _collect()
    assert not named, (
        "Именованная ссылка на раздел — запрещена, ссылайся по номеру:\n  "
        + "\n  ".join(named)
        + "\nЗаголовок это редакция и переписывается; номер §-ID объявлен "
          "неизменным (CLAUDE.md, шапка)."
    )


def test_sentinel_sees_its_own_targets():
    """Позитивный контроль: сторож действительно читает оба файла.

    Без него пустой разбор заголовков дал бы вечно-зелёный результат —
    ровно тот ложно-зелёный, против которого стоит §20.
    """
    assert len(_claude_sections()) >= 15, "§-ID свода не разобраны"
    assert len(_tracked()) > 100, "периметр сторожа пуст"
    # BLUEPRINT намеренно не проверяется на непустоту: он закрыт, разделов нет,
    # и требование «их должно быть ≥5» само стало бы мёртвым утверждением (§18).
    # Живость разбора заголовков доказывает CLAUDE.md выше — код разбора общий.


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
