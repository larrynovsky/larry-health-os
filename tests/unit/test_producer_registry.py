"""Тесты producer_registry — ратчет покрытия scheduled-производителей.

Enforcement feedback_context_gate_orphan: задача без датчика/exempt = находка;
протухший ключ реестра = находка (census-симметрия). Red-path проверен нарочно.
"""

import producer_registry as pr


def test_all_classified_clean():
    labels = list(pr.MONITORED) + list(pr.EXEMPT)
    assert pr.audit_producers(labels) == []


def test_unclassified_job_flagged():
    labels = list(pr.MONITORED) + list(pr.EXEMPT) + ["com.larry.health.NEW-untracked"]
    found = pr.audit_producers(labels)
    assert len(found) == 1  # только unclassified; реестр не протух
    assert "NEW-untracked" in found[0] and "без датчика" in found[0]


def test_orphan_intersection_case_is_caught():
    # Прямой сценарий BL-DOCAGENT: задача существует, но нигде не классифицирована
    found = pr.audit_producers(["com.larry.health.doc-agent-like"])
    assert any("doc-agent-like" in f for f in found)


def test_stale_registry_key_flagged():
    # MONITORED ссылается на задачу, которой больше нет в launchd
    labels = [l for l in pr.MONITORED if l != "com.larry.health.backup"] + list(pr.EXEMPT)
    found = pr.audit_producers(labels)
    assert len(found) == 1 and "протух" in found[0] and "backup" in found[0]


def test_both_findings_independent():
    labels = ["com.larry.health.NEW-x"]  # ничего из реестра + новый
    found = pr.audit_producers(labels)
    assert len(found) == 2  # unclassified NEW-x + весь реестр протух
    assert any("без датчика" in f for f in found)
    assert any("протух" in f for f in found)


def test_scan_filters_keepalive(tmp_path):
    (tmp_path / "com.larry.health.sched.plist").write_text(
        "<key>Label</key><string>com.larry.health.sched</string>"
        "<key>StartCalendarInterval</key>"
    )
    (tmp_path / "com.larry.health.daemon.plist").write_text(
        "<key>Label</key><string>com.larry.health.daemon</string>"
        "<key>KeepAlive</key><true/>"
    )
    labels = pr._scan_scheduled_labels(tmp_path)
    assert labels == ["com.larry.health.sched"]  # KeepAlive отфильтрован


def test_collect_off_host_empty(tmp_path):
    # Не-Studio (каталога нет) → пусто, не падение
    assert pr.collect_producer_findings(tmp_path / "нет") == []


# ── Покрытие, вычисленное из кода (23.09, нить producer-census) ─────────────────
# Дважды (07.08, 07.09) ратчет кричал о задачах, которые были покрыты: строку в MONITORED
# забывали внести. Для датчиков на `_schedule_covers(<метка>)` покрытие читается из кода.

_SRC = '''
LABEL_A = "com.larry.health.a"

def check_a():
    covered, fire = _schedule_covers(LABEL_A, "2026-09-23")

def check_b_literal():
    _schedule_covers("com.larry.health.b", "x")

def check_unregistered():
    _schedule_covers("com.larry.health.c", "x")

check("а", check_a)
check("б", check_b_literal)
'''


def test_schedule_judged_labels_reads_constants_literals_and_registration(tmp_path):
    src = tmp_path / "integrity_tests.py"
    src.write_text(_SRC, encoding="utf-8")
    got = pr.schedule_judged_labels(src)
    assert got == {"com.larry.health.a": ["check_a"], "com.larry.health.b": ["check_b_literal"]}, \
        "незарегистрированная проверка ничего не покрывает"


def test_live_code_covers_the_three_schedule_judged_jobs():
    """На живом integrity_tests.py: те три задачи, что ушли из MONITORED 23.09."""
    got = pr.schedule_judged_labels()
    for label in ("com.larry.health.longitudinal", "com.larry.health.night-cycle",
                  "com.larry.health.owner-nag"):
        assert got.get(label), f"{label}: вычисленного покрытия нет — задача снова без датчика"


def test_computed_coverage_classifies_the_job():
    labels = list(pr.MONITORED) + list(pr.EXEMPT) + ["com.larry.health.a"]
    assert pr.audit_producers(labels, {"com.larry.health.a": ["check_a"]}) == []


def test_manual_duplicate_of_computed_coverage_is_flagged():
    key = next(iter(pr.MONITORED))
    found = pr.audit_producers(list(pr.MONITORED) + list(pr.EXEMPT), {key: ["check_x"]})
    assert len(found) == 1 and "записано и руками" in found[0] and key in found[0]
