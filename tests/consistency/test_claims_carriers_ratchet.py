"""Ратчет носителей: объявленное однажды не исчезает молча.

Без него механизм вырождается в вечно-зелёный: стёрли все метки — ноль
утверждений, ноль расхождений, прогон зелёный. Ратчет по НОСИТЕЛЯМ, а не по
номерам строк: свод переписывают, строки едут, носители — нет.
Новая метка ратчет проходит; исчезнувшая — красит. Красное требует решения
человека (носитель удалён осознанно → вычеркни из baseline), а не правки теста.
"""
from pathlib import Path

import pytest

import affected_claims as ac

ROOT = Path(__file__).resolve().parents[2]

BASELINE = {
    "backup.sh",
    "backup_studio.sh",
    "check_contracts.py",
    "com.larry.health.uncommitted-watchdog.plist",
    "gen_arch_blocks.py",
    "gen_key_paths.py",
    "gen_schedule.py",
    "gen_testing_contracts.py",
    "health_db.py",
    "integrity_tests.py",
    "lab_intake_watcher.py",
    "lab_promote.py",
    "labs_db.py",
    "owner_gate.py",
    "project_context/dispgate.py",
    "project_context/disposability.json",
    "project_context/staged.py",
    "run_checks.sh",
    "scripts/git-hooks/post-commit-macbook",
    "scripts/git-hooks/pre-commit",
    "scripts/git-hooks/pre_commit_check.py",
    "scripts/pre_destructive_check.sh",
    "scripts/uncommitted_watchdog.py",
    "tests/consistency/test_single_canonical_db.py",
    "tests/integration/test_dispgate_hook.py",
    "tests/unit/test_dispgate.py",
    "tests/unit/test_health_db_signatures_contract.py",
    "tests/unit/test_lab_history_current.py",
    "tests/unit/test_lab_intake_watcher.py",
    "tests/unit/test_lab_promote.py",
    "tests/unit/test_owner_gate.py",
}


# Пол покрытия. Владелец 2026-08-20 выбрал «голос только при деградации»: тишина,
# пока покрытие держится или растёт, красное — в момент падения. Число, а не доля:
# доля краснела бы при КАЖДОМ добавлении нормы, то есть была бы блоком с отсрочкой,
# а блок владелец отверг сознательно (ложная метка хуже отсутствующей).
COVERAGE_FLOOR = 12


def _live():
    claims, errors = ac.parse_claims((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    return claims, errors


def test_svod_metki_parse_without_errors():
    _, errors = _live()
    assert errors == [], f"битые метки в своде: {errors}"


@pytest.mark.owner_data
def test_declared_carriers_all_exist_on_disk():
    claims, _ = _live()
    assert ac.unresolved_carriers(claims, ROOT) == []


def test_baseline_carriers_still_declared():
    claims, _ = _live()
    live = {p for c in claims for p in c.carriers}
    missing = BASELINE - live
    assert not missing, (
        f"носители исчезли из свода: {sorted(missing)}. "
        "Если это осознанно (утверждение снято) — вычеркни их из BASELINE тем же коммитом.")


def test_live_coverage_does_not_degrade():
    """Покрытие строк «Enforcement: LIVE» метками не падает ниже пола.

    Не требует размечать новое — метка добровольна по решению владельца. Требует
    не терять размеченное: снятая метка обязана быть решением, а не тишиной.
    Растёт пол — подними COVERAGE_FLOOR тем же коммитом.
    """
    have, total = ac.live_coverage((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert have >= COVERAGE_FLOOR, (
        f"покрытие меток упало: {have} из {total}, пол {COVERAGE_FLOOR}. "
        "Метку сняли осознанно — опусти COVERAGE_FLOOR тем же коммитом и скажи почему.")
