"""Тесты для integrity_tests.py [11] — check_backup_freshness + check_db_size.

RST: проверяем граничные случаи, которые счастливый прогон на Studio не покрывает.
  - пустая backup директория
  - устаревший бэкап (старше SLA)
  - свежий бэкап (happy path)
  - DB size: WARN порог
  - DB size: FAIL порог
  - ротация: пороги живости (квитанции нет / молчит сутки / самопроверка не прошла)
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.unit


# ── scan_backup_staleness (тенант-осознанный, 2026-07-10) ──────────────────────

def _tenant_db(tmp_path, name):
    d = tmp_path / "bkstale" / name / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "health.db"
    p.write_text("")
    return p


def _backup(db_path, date_name, fresh=True):
    """Файл бэкапа с mtime ровно в последний плановый запуск (fresh) или за минуту до него.
    С 23.09 свежесть = «покрывает последний плановый запуск» по плисту (tests/conftest.py:
    com.larry.health.backup в 03:00), а не «моложе 25 ч»."""
    from datetime import datetime
    import plist_env_liveness as pl
    root = db_path.parent.parent
    bd = root / "backups"
    bd.mkdir(parents=True, exist_ok=True)
    f = bd / f"{root.name}_{date_name}.db"
    f.write_text("")
    fire = pl.last_scheduled_fire("com.larry.health.backup", datetime.now())
    t = fire.timestamp() - (0 if fresh else 60)
    os.utime(str(f), (t, t))
    return f


def test_backup_scan_flags_missing_tenant(tmp_path):
    """Тенант без единого ежедневного бэкапа → 'missing'. Ровно слепой угол 2026-07-10:
    у партнёра не было бэкапов, а датчик молчал."""
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    partner = _tenant_db(tmp_path, "health_partner")
    _backup(owner, "2026-07-10")  # owner свеж; у партнёра бэкапов нет
    bad = it.scan_backup_staleness([owner, partner])
    assert any(t == "health_partner" and k == "missing" for t, k, _ in bad)
    assert not any(t == "health" for t, _, _ in bad)


def test_backup_scan_flags_stale(tmp_path):
    """Бэкап старше последнего планового запуска (хотя бы на минуту) → 'stale'.
    До 23.09 порог был 25 ч; «общее правило» владельца — тревога после первого пропуска."""
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    _backup(owner, "2026-01-01", fresh=False)
    bad = it.scan_backup_staleness([owner])
    assert any(t == "health" and k == "stale" for t, k, _ in bad)


def test_backup_scan_fresh_silent(tmp_path):
    """Свежие бэкапы у всех тенантов → пусто."""
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    partner = _tenant_db(tmp_path, "health_partner")
    _backup(owner, "2026-07-10")
    _backup(partner, "2026-07-10")
    assert it.scan_backup_staleness([owner, partner]) == []


def test_backup_scan_ignores_wal_shm(tmp_path):
    """.db-shm/.db-wal не считаются бэкапом → тенант помечается 'missing'."""
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    bd = owner.parent.parent / "backups"
    bd.mkdir(parents=True, exist_ok=True)
    (bd / "health_2026-07-10.db-shm").write_text("")
    (bd / "health_2026-07-10.db-wal").write_text("")
    bad = it.scan_backup_staleness([owner])
    assert any(t == "health" and k == "missing" for t, k, _ in bad)


# ── check_db_size ──────────────────────────────────────────────────────────────

def test_db_size_warns_when_over_warn_limit(tmp_path, monkeypatch):
    """DB больше DB_SIZE_WARN_MB → warn() вызван, исключения нет."""
    import integrity_tests as it
    import health_db as db_module

    fake_db = tmp_path / "health.db"
    fake_db.touch()
    monkeypatch.setattr(db_module, "DB_PATH", fake_db)

    # Подменяем размер файла через stat
    import pathlib
    original_stat = pathlib.Path.stat

    def fake_stat(self, **kw):
        result = original_stat(self, **kw)
        # Возвращаем stat с размером 350MB
        import os
        return os.stat_result((
            result.st_mode, result.st_ino, result.st_dev, result.st_nlink,
            result.st_uid, result.st_gid,
            350 * 1024 * 1024,  # st_size = 350MB
            result.st_atime, result.st_mtime, result.st_ctime,
        ))

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)

    initial_warns = len(it._warnings)
    it.check_db_size()  # не должно бросить AssertionError
    new_warns = it._warnings[initial_warns:]
    assert any("300" in w[1] or "350" in w[1] for w in new_warns), (
        f"Ожидали warn о размере, получили: {new_warns}"
    )


def test_db_size_fails_when_over_fail_limit(tmp_path, monkeypatch):
    """DB больше DB_SIZE_FAIL_MB → AssertionError с упоминанием FAIL лимита."""
    import integrity_tests as it
    import health_db as db_module

    fake_db = tmp_path / "health.db"
    fake_db.touch()
    monkeypatch.setattr(db_module, "DB_PATH", fake_db)

    import pathlib
    original_stat = pathlib.Path.stat

    def fake_stat(self, **kw):
        result = original_stat(self, **kw)
        import os
        return os.stat_result((
            result.st_mode, result.st_ino, result.st_dev, result.st_nlink,
            result.st_uid, result.st_gid,
            600 * 1024 * 1024,  # st_size = 600MB
            result.st_atime, result.st_mtime, result.st_ctime,
        ))

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)

    with pytest.raises(AssertionError, match="FAIL лимит"):
        it.check_db_size()


# ── check_logrotate_liveness: пороги (факты покрыты test_log_rotate.py) ───────
# Здесь проверяется РЕШЕНИЕ по фактам, а не сбор фактов: какой исход краснеет.

def _verdict(monkeypatch, **status):
    import integrity_tests as it
    import log_rotate
    monkeypatch.setattr(log_rotate, "receipt_status", lambda *a, **k: status)
    return it


def test_logrotate_missing_receipt_fails(monkeypatch):
    it = _verdict(monkeypatch, exists=False, age_h=None, selftest_ok=False)
    with pytest.raises(AssertionError, match="не запускался ни разу"):
        it.check_logrotate_liveness()


def test_logrotate_day_of_silence_fails(monkeypatch):
    it = _verdict(monkeypatch, exists=True, age_h=30.0, selftest_ok=True)
    with pytest.raises(AssertionError, match="молчит"):
        it.check_logrotate_liveness()


def test_logrotate_alive_but_selftest_failed_is_not_green(monkeypatch):
    """Живой, но сломанный: heartbeat свежий, а самопроверка copytruncate не прошла (§14)."""
    it = _verdict(monkeypatch, exists=True, age_h=0.1, selftest_ok=False)
    with pytest.raises(AssertionError, match="selftest ok"):
        it.check_logrotate_liveness()


def test_logrotate_fresh_and_clean_is_green(monkeypatch):
    """Негативный контроль против датчика, который краснеет всегда."""
    it = _verdict(monkeypatch, exists=True, age_h=0.4, selftest_ok=True)
    assert it.check_logrotate_liveness() == {"logrotate_age_h": 0.4}


# ── check_secret_scope_matches_reality: пороги решения (02.09) ────────────────
# Факты (какие файлы лежат) — окружение; здесь проверяется ВЕРДИКТ по фактам.

def _scope_env(monkeypatch, tmp_path, owner_names, partner_names=None, scope=None):
    import integrity_tests as it
    import secrets_paths
    o = tmp_path / ".health_secrets"; o.mkdir()
    for n in owner_names:
        (o / n).write_text("x")
    if partner_names is not None:
        pt = tmp_path / ".health_secrets_partner"; pt.mkdir()
        for n in partner_names:
            (pt / n).write_text("x")
    monkeypatch.setattr(it.Path, "home", staticmethod(lambda: tmp_path))
    if scope is not None:
        monkeypatch.setattr(secrets_paths, "SECRET_SCOPE", scope)
    return it


def test_secret_without_declared_class_is_caught(monkeypatch, tmp_path):
    it = _scope_env(monkeypatch, tmp_path, ["brand_new_token"], scope={})
    with pytest.raises(AssertionError, match="класс не объявлен"):
        it.check_secret_scope_matches_reality()


def test_owner_secret_appearing_at_partner_is_caught(monkeypatch, tmp_path):
    """Выгода гибрида: расхождение объявления с фактом — сигнал, а не шум."""
    it = _scope_env(monkeypatch, tmp_path, ["anthropic_key"], partner_names=["anthropic_key"],
                    scope={"anthropic_key": "owner"})
    with pytest.raises(AssertionError, match="есть у партнёра"):
        it.check_secret_scope_matches_reality()


def test_matching_reality_is_green(monkeypatch, tmp_path):
    """Негативный контроль: датчик не краснеет, когда всё сходится."""
    it = _scope_env(monkeypatch, tmp_path, ["anthropic_key", "telegram_token"],
                    partner_names=["telegram_token"],
                    scope={"anthropic_key": "owner", "telegram_token": "tenant"})
    assert it.check_secret_scope_matches_reality()["secrets_checked"] == 2


def test_no_partner_dir_means_silence_not_verdict(monkeypatch, tmp_path):
    """Границу судим честно: нет каталога партнёра — молчим, а не выдумываем."""
    it = _scope_env(monkeypatch, tmp_path, ["telegram_token"], scope={"telegram_token": "tenant"})
    assert it.check_secret_scope_matches_reality()["partner_dir"] is False


def test_unrestarted_daemon_is_a_failure_not_a_warning(monkeypatch):
    """Решение владельца 06.10 «делать» (урок C-166): служба, которую деплой не перезапускает, —
    FAIL ночного монитора, а не warn, которого шесть суток никто не читал. Мутация: вернуть warn."""
    import daemon_liveness
    import integrity_tests as it
    monkeypatch.setattr(daemon_liveness, "find_unrestarted_daemons",
                        lambda: ["com.larry.health.lab-intake.partner"])
    before_fail, before_warn = it.FAIL, it.WARN
    out = it.check_deploy_restart_completeness()
    assert out == {"unrestarted": ["com.larry.health.lab-intake.partner"]}
    assert it.FAIL == before_fail + 1 and it.WARN == before_warn
