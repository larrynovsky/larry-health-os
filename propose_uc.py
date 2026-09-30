#!/usr/bin/env python3.11
"""
propose_uc.py — UC-J-01: анализ git-diff и предложение proposal UC.

Запускается через post-commit hook. Анализирует diff последнего коммита,
если затронуты модули из B-скоупа (Tier-1 + ключевые user-facing) — пишет
файл `tests/plans/proposed/{TIMESTAMP}_proposal.md` со списком предлагаемых
UC-изменений.

**ВАЖНО (UC-J-01):** ничего НЕ пишет в `USE_CASES.md` сам.
Решение принимает человек, читая proposal и применяя его вручную (или через
будущий `apply_proposal.py`).

CLI:
    python3.11 propose_uc.py [--commit HEAD] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from _time_inject import get_now

PROPOSED_DIR = SCRIPT_DIR / "tests" / "plans" / "proposed"
PROPOSED_DIR.mkdir(parents=True, exist_ok=True)


# ── Маппинг файла → группа UC ────────────────────────────────────────────────
# Когда меняется файл — это сигнал что соответствующая группа UC может
# нуждаться в новом proposal или ревью.

FILE_TO_UC_GROUP: dict[str, list[str]] = {
    "import_all.py": ["A"],
    "import_oura.py": ["A"],
    "import_apple_health.py": ["A"],
    "calendar_sync.py": ["A"],
    "import_medical_docs.py": ["A"],
    "lab_extractor.py": ["A"],

    "gp_agent.py": ["B"],
    "lifestyle_agents.py": ["B"],
    "checkin_agent.py": ["B"],
    "longitudinal_analysis.py": ["B"],
    "hai_reports.py": ["B"],

    "genome_parser.py": ["C"],
    "genome_annotator.py": ["C"],
    "genome_context.py": ["C"],
    "genome_update_agent.py": ["C"],

    "safety_net.py": ["D"],
    "triage_agent.py": ["D"],

    "generate_constitutions.py": ["E"],
    "constitution_analysis.py": ["E"],

    "hai_hypotheses.py": ["F"],
    "hai_analysis.py": ["F"],

    "task_agent.py": ["G"],
    "reminders_sync.py": ["G"],

    "wellally_consult.py": ["H"],

    "telegram_bot.py": ["I"],
    "hai_core.py": ["I"],
    "health_db.py": ["I"],

    "check_wellally_updates.py": ["K"],
}


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(SCRIPT_DIR), *args],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def get_diff_files(commit: str = "HEAD") -> list[str]:
    """Возвращает список изменённых файлов в коммите."""
    out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    return [f.strip() for f in out.splitlines() if f.strip()]


def get_diff_text(commit: str = "HEAD", max_lines: int = 200) -> str:
    out = _git("show", "--stat", commit)
    lines = out.splitlines()[:max_lines]
    return "\n".join(lines)


def classify_changes(files: list[str]) -> dict[str, list[str]]:
    """Возвращает {группа_UC: [файлы]}. Файлы вне маппинга — в 'unknown'."""
    by_group: dict[str, list[str]] = {}
    for f in files:
        name = Path(f).name
        groups = FILE_TO_UC_GROUP.get(name)
        if groups:
            for g in groups:
                by_group.setdefault(g, []).append(f)
        else:
            # Файлы вне маппинга — игнорируем (тесты, docs, configs).
            if not (name.startswith("test_") or f.startswith("tests/") or
                    name.endswith(".md") or name.endswith(".plist") or
                    name.endswith(".sh") or name.endswith(".sql")):
                by_group.setdefault("unknown", []).append(f)
    return by_group


def build_proposal(commit: str = "HEAD") -> Optional[dict]:
    """Возвращает proposal-структуру или None если diff пустой/не релевантен."""
    files = get_diff_files(commit)
    if not files:
        return None
    by_group = classify_changes(files)
    relevant = {g: fs for g, fs in by_group.items() if g != "unknown"}
    if not relevant:
        return None  # ни одной B-скоуп группы не затронуто

    sha = _git("rev-parse", commit)[:12]
    msg = _git("log", "-1", "--format=%s", commit)
    return {
        "commit": sha,
        "message": msg,
        "timestamp": get_now().isoformat(),
        "files": files,
        "groups_affected": list(relevant.keys()),
        "by_group": relevant,
        "stat": get_diff_text(commit),
    }


def write_proposal_md(proposal: dict) -> Path:
    """Пишет markdown в tests/plans/proposed/."""
    ts = proposal["timestamp"][:19].replace(":", "-")
    sha = proposal["commit"]
    path = PROPOSED_DIR / f"{ts}_{sha}.md"

    md_lines = [
        f"# Proposal UC: коммит {sha}",
        "",
        f"**Сообщение коммита:** {proposal['message']}",
        f"**Timestamp:** {proposal['timestamp']}",
        f"**Затронуты группы UC:** {', '.join(proposal['groups_affected'])}",
        "",
        "## Изменённые файлы",
        "",
    ]
    for g, files in proposal["by_group"].items():
        md_lines.append(f"### Группа {g}")
        for f in files:
            md_lines.append(f"- `{f}`")
        md_lines.append("")

    md_lines.extend([
        "---",
        "",
        "## Что делать",
        "",
        "1. Ревью этого diff — есть ли изменения, которые **меняют поведение**, "
        "описанное в `USE_CASES.md` для затронутых групп?",
        "2. Если да — обновить соответствующий UC (Then/B/E) **руками** "
        "в `USE_CASES.md`. Может потребоваться пересмотр `confirmation` "
        "(возможно стоит вернуть в `proposed` для повторного ревью).",
        "3. Если изменения не меняют контракт — закрыть этот файл (move в archive/).",
        "",
        "**Важно:** этот файл — **proposal**, не запись в каталоге UC.",
        "В `USE_CASES.md` ничего не записывается автоматически (UC-J-01 правило).",
        "",
        "## Diff stat",
        "",
        "```",
        proposal["stat"],
        "```",
    ])

    path.write_text("\n".join(md_lines), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--commit", default="HEAD")
    p.add_argument("--dry-run", action="store_true",
                    help="не пишет файл, только печатает proposal в JSON")
    args = p.parse_args()

    proposal = build_proposal(args.commit)
    if proposal is None:
        print(json.dumps({"status": "no_relevant_changes",
                           "commit": args.commit}, ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0

    path = write_proposal_md(proposal)
    print(json.dumps({"status": "written",
                       "path": str(path),
                       "groups": proposal["groups_affected"]},
                       ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
