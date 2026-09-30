"""Тёплая страница не указывает читателю на файл, которого нет.

Находка 2026-09-02: 15 генерируемых intent-страниц отправляли читателя в `BLUEPRINT.md`,
удалённый 2026-08-04 нитью docs-homes. Промпт генератора такого файла НЕ называет (он велит
упомянуть head_file, spec и subsystem_intent.yaml) — имя дописала модель, из собственных
представлений о проекте. То есть в производном слое живёт вымысел, и он ссылается на
несуществующее месяц.

ПЕРИМЕТР — только СГЕНЕРИРОВАННЫЕ страницы (несут метку intent-provenance). Планы в том же
каталоге законно описывают ещё не созданное — замер 02.09: 28 «несуществующих» путей, и все
в планах. Сторож, красный на легаси-планах, выучили бы игнорировать (§13).

ЧЕГО ЭТОТ СТОРОЖ НЕ ЛОВИТ: путь, который существует, но не о том. Ссылка живая и неверная —
суждение, а не предикат.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "docs" / "explanation"
_PATH = re.compile(r'`([A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|sh|json))`')


def _generated_pages() -> list[Path]:
    return [p for p in sorted(PAGES.glob("*.md"))
            if "<!-- intent-provenance:" in p.read_text(encoding="utf-8")]


def _dangling(text: str) -> list[str]:
    """Чистая функция — её и проверяет позитивный контроль на синтетике."""
    out = []
    for m in _PATH.finditer(text):
        name = m.group(1)
        if (ROOT / name).exists():
            continue
        if list(ROOT.rglob(Path(name).name)):   # упомянут базовым именем
            continue
        out.append(name)
    return sorted(set(out))


def test_positive_control_catches_a_dead_pointer():
    """Сторож обязан краснеть на выдуманном пути (RST)."""
    assert _dangling("смотри `BLUEPRINT_NEVER_EXISTED.md`") == ["BLUEPRINT_NEVER_EXISTED.md"]
    assert _dangling("смотри `subsystem_intent.yaml`") == []


@pytest.mark.owner_data
def test_generated_pages_have_no_dead_pointers():
    pages = _generated_pages()
    assert pages, "не нашлось ни одной генерируемой страницы — периметр пуст, сторож слеп"
    bad = {p.name: _dangling(p.read_text(encoding="utf-8")) for p in pages}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, (
        f"тёплые страницы отправляют читателя в несуществующее: {bad}. "
        "Либо путь устарел (файл переехал/удалён), либо модель его выдумала — "
        "второе и случилось с BLUEPRINT.md, прожившим в 15 страницах месяц после удаления.")
