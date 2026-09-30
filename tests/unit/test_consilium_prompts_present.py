"""Датчик: у каждого участника консилиума есть реальный промпт-файл.

Инцидент (найден обзором 2026-07-02): миграция промптов iCloud→git (Ф0b,
2026-06-18) обновила wellally_consult, но пропустила monthly_consilium —
его SPEC_DIR указывал на заархивированный iCloud-каталог, `_read_medical_prompt`
тихо подставлял заглушку «Ты — специалист в области X». Консилиум 2026-07-01
отработал на заглушках. Классы: «миграция должна покрыть всех читателей» +
«fallback требует датчика».

Проверяем в точке потребления: оба движка резолвят промпты из git-каталога,
и каждое имя из единого ростера имеет файл.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]


def _roster() -> list[str]:
    import consilium_roster
    return list(consilium_roster.medical_roster())


def test_monthly_consilium_spec_dir_in_repo():
    import monthly_consilium as mc
    spec = Path(mc.SPEC_DIR).resolve()
    assert _REPO in spec.parents or spec == _REPO / "specialists", (
        f"monthly_consilium.SPEC_DIR вне репозитория: {spec} — "
        "промпты живут в git (Ф0b), iCloud-каталог заархивирован")
    assert "Mobile Documents" not in str(spec)


def test_wellally_consult_spec_dir_in_repo():
    import wellally_consult as wc
    spec = Path(wc.SPEC_DIR).resolve()
    assert "Mobile Documents" not in str(spec)


def test_every_roster_member_has_prompt_file():
    import monthly_consilium as mc
    missing = [n for n in _roster()
               if not (Path(mc.SPEC_DIR) / f"{n}.md").exists()]
    assert not missing, (
        f"Нет промпт-файлов для участников ростера: {missing}. "
        "Без файла _read_medical_prompt деградирует на заглушку — "
        "консилиум получает специалиста без конституции.")
