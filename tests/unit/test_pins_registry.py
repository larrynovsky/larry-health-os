"""Ратчет реестра пинов (обзор трёх линз, 2026-07-02).

135 характеризационных пинов фиксируют поведение «как есть», не отличая
bug-frozen от design-frozen. Полный вердикт-свип непропорционален; вместо него
baseline-ратчет: НОВЫЕ характеризационные файлы обязаны декларировать
PIN-VERDICT (design|bug|unknown), чтобы класс «окаменевший баг» не рос.
Конвенция и реестр bug-пинов — tests/PINS.md.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_UNIT = Path(__file__).resolve().parent
_REPO = _UNIT.parents[1]

# Baseline: 21 характеризационный файл на момент установки конвенции.
# Их пины grandfathered как design; пересмотр — оппортунистический.
_BASELINE = {
    "test_agent_reports_characterization.py", "test_b_tail_characterization.py",
    "test_checkins_characterization.py", "test_clinical_misc_characterization.py",
    "test_config_lookups_characterization.py",
    "test_constitutions_proposals_characterization.py",
    "test_genome_characterization.py",  # test_experiments_characterization.py удалён (BL-EXP-1, 2026-07-10)
    "test_lab_meta_misc_characterization.py", "test_labs_characterization.py",
    "test_lifecycle_readers_characterization.py",
    "test_lifecycle_transitions_characterization.py",
    "test_memory_characterization.py", "test_metrics_characterization.py",
    "test_misc_utils_characterization.py",
    "test_problems_periods_characterization.py",
    "test_protocols_characterization.py",
    "test_reviews_outcomes_characterization.py",
    "test_sessions_profile_events_characterization.py",
    "test_tasks_characterization.py", "test_write_utils_characterization.py",
}


def test_pins_doc_exists():
    assert (_REPO / "tests" / "PINS.md").exists(), "tests/PINS.md — реестр пинов"


def test_new_characterization_files_declare_verdict():
    """Файл *_characterization.py вне baseline обязан нести хотя бы одну
    строку PIN-VERDICT. Ловит рост класса «пин без объявленного намерения»."""
    offenders = []
    for f in _UNIT.glob("*_characterization.py"):
        if f.name in _BASELINE:
            continue
        if "PIN-VERDICT" not in f.read_text(encoding="utf-8"):
            offenders.append(f.name)
    assert not offenders, (
        f"Новые характеризационные файлы без PIN-VERDICT: {offenders}. "
        "Объяви намерение пинов (design|bug|unknown) — см. tests/PINS.md.")


def test_baseline_files_still_exist():
    """Baseline-симметрия: если grandfathered-файл переименован/удалён —
    обнови _BASELINE и PINS.md (иначе список врёт)."""
    missing = sorted(n for n in _BASELINE if not (_UNIT / n).exists())
    assert not missing, f"Baseline-файлы исчезли, обнови реестр: {missing}"
