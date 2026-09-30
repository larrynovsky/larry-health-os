"""epistemic_skill.loader — собирает текст дисциплины нарратора и версию (sha12).

Версия = "epi_" + sha256(содержимое)[:12]. Правка любого файла меняет версию —
её можно штамповать в строку артефакта (provenance).
Fail-fast: отсутствие файла → RuntimeError (а не тихий пустой промпт).

load_skill()           — конституции (ORDER, по умолчанию discipline.txt).
load_part(filename)    — один именованный файл (например coordinator.txt).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_DIR = Path(__file__).parent
ORDER = ("discipline.txt",)


class Skill:
    __slots__ = ("text", "version", "files")

    def __init__(self, text: str, version: str, files: tuple[str, ...]):
        self.text = text
        self.version = version
        self.files = files


def _make(text: str, files: tuple[str, ...]) -> Skill:
    version = "epi_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return Skill(text, version, files)


def load_part(filename: str) -> Skill:
    p = _DIR / filename
    if not p.exists():
        raise RuntimeError(f"epistemic_skill: отсутствует {filename} в {_DIR}")
    return _make(p.read_text(encoding="utf-8"), (filename,))


def load_skill() -> Skill:
    parts = []
    for name in ORDER:
        p = _DIR / name
        if not p.exists():
            raise RuntimeError(f"epistemic_skill: отсутствует {name} в {_DIR}")
        parts.append(p.read_text(encoding="utf-8"))
    return _make("\n".join(parts), ORDER)
