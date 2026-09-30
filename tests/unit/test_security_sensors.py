"""Тесты security_sensors (SEC-чеклист как код, 2026-07-06).

Покрытие: каждая категория happy/violation + инвариант «никогда не бросает»
+ доставка (security:* не в mute-list триажа — anti «детект без доставки»).
"""

import infra_config
import json
import subprocess
import time

import pytest

import security_sensors as ss
from triage_agent import classify_warnings


# ── db_perms ──────────────────────────────────────────────────────────────────

def _mk(p, mode, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    p.chmod(mode)
    return p


def test_db_perms_flags_group_readable(tmp_path):
    bad = _mk(tmp_path / "health" / "backups" / "snap.db", 0o644)
    _mk(tmp_path / "health" / "backups" / "ok.db", 0o600)
    found = ss._db_perms(tmp_path)
    assert len(found) == 1 and str(bad) in found[0] and "644" in found[0]


def test_db_perms_covers_partner_tenant_and_ignores_non_db(tmp_path):
    _mk(tmp_path / "health_partner" / "backups" / "p.db", 0o644)
    _mk(tmp_path / "health" / "data" / "notes.txt", 0o644)  # не .db — не наш периметр
    found = ss._db_perms(tmp_path)
    assert len(found) == 1 and "health_partner" in found[0]


# ── secrets_perms ─────────────────────────────────────────────────────────────

def test_secrets_dir_755_flagged_and_loose_file_too(tmp_path):
    """Права каталога и права файлов — две отдельные находки.

    Переписан 2026-08-03: раньше тест проверял, что heartbeat вотчдога прощён
    по allowlist. Файл переехал в logs/ (его ISO-timestamp становился «иглой»
    secret_guard), allowlist опустел, и прощать стало нечего — теперь любой
    не-600 файл в каталоге секретов это находка, а не исключение.
    """
    d = tmp_path / ".health_secrets"
    _mk(d / "token", 0o600)
    _mk(d / "loose", 0o644)          # больше не прощается: allowlist пуст
    d.chmod(0o755)
    found = ss._secrets_perms(tmp_path)
    assert any("755" in f and "канон 700" in f for f in found), found
    assert any("loose" in f for f in found), found


def test_secrets_clean_when_canonical(tmp_path):
    d = tmp_path / ".health_secrets"
    _mk(d / "token", 0o600)
    d.chmod(0o700)
    assert ss._secrets_perms(tmp_path) == []


def test_secrets_world_readable_file_flagged(tmp_path, monkeypatch):
    d = tmp_path / ".neighbour_secrets"
    monkeypatch.setattr(infra_config, "NEIGHBORS", {"crm": {"secrets": d}})
    _mk(d / "smtp.env", 0o644)
    d.chmod(0o700)
    found = ss._secrets_perms(tmp_path)
    assert len(found) == 1 and "smtp.env" in found[0]


def test_neighbor_permissions_empty_or_configured(tmp_path, monkeypatch):
    db = _mk(tmp_path / "crm" / "backups" / "crm_snapshot.db", 0o644)
    secret = _mk(tmp_path / ".crm_secrets" / "token", 0o644)
    secret.parent.chmod(0o700)
    assert ss._db_perms(tmp_path) == [] and ss._secrets_perms(tmp_path) == []
    monkeypatch.setattr(infra_config, "NEIGHBORS", infra_config._neighbors({
        "crm": {"path": str(tmp_path / "crm"), "db": "crm.db", "secrets": str(secret.parent)},
        "lab-notes": {"path": str(tmp_path / "lab-notes"), "lessons": "lessons.yaml"},
    }))
    assert len(ss._db_perms(tmp_path)) == 1 and str(db) in ss._db_perms(tmp_path)[0]
    assert len(ss._secrets_perms(tmp_path)) == 1 and str(secret) in ss._secrets_perms(tmp_path)[0]
    log = []
    assert ss.repair_db_perms(tmp_path, log.append) == [str(db)]
    assert ss._db_perms(tmp_path) == [] and db.stat().st_mode & 0o777 == 0o600
    assert log and str(db) in log[0]


# ── listen_ports (чистый парсер, канонический вывод lsof -nP) ─────────────────

_LSOF_SAMPLE = f"""\
COMMAND   PID  USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
ControlCe 634 zz      10u  IPv4 0x0        0t0  TCP *:5000 (LISTEN)
ControlCe 634 zz      11u  IPv6 0x0        0t0  TCP *:7000 (LISTEN)
Python    999 zz       5u  IPv4 0x0        0t0  TCP {infra_config.STUDIO_HOST}:8001 (LISTEN)
Python    999 zz       6u  IPv4 0x0        0t0  TCP 127.0.0.1:8000 (LISTEN)
rapportd  500 zz       8u  IPv4 0x0        0t0  TCP *:49156 (LISTEN)
evil      666 zz       3u  IPv4 0x0        0t0  TCP *:2222 (LISTEN)
sneaky    667 zz       4u  IPv4 0x0        0t0  TCP 0.0.0.0:9999 (LISTEN)
"""


def test_listen_parser_flags_only_unknown_nonloopback():
    found = ss._parse_listen(_LSOF_SAMPLE)
    assert len(found) == 2
    assert any("evil" in f and ":2222" in f for f in found)
    assert any("sneaky" in f and ":9999" in f for f in found)
    # allowlist, loopback и ephemeral не всплывают
    joined = " ".join(found)
    for quiet in (":5000", ":7000", ":8001", ":8000", ":49156"):
        assert quiet not in joined


def test_listen_parser_dedups_ipv4_ipv6():
    dup = _LSOF_SAMPLE + "evil      666 zz    4u IPv6 0x0 0t0 TCP [::]:2222 (LISTEN)\n"
    found = ss._parse_listen(dup)
    assert sum(":2222" in f for f in found) == 1


def test_listen_colima_dns_allowed_only_for_limactl():
    """23.09: разрешён проброс DNS виртуальной машины Colima (limactl на *:53) — и только
    он. Другой процесс на том же порту — открытый DNS-сервер, о нём сторож звенит."""
    lines = ("limactl 7659 zz    9u IPv6 0x0 0t0 TCP *:53 (LISTEN)\n"
             "dnsmasq 7777 zz    9u IPv4 0x0 0t0 TCP *:53 (LISTEN)\n")
    found = ss._parse_listen(_LSOF_SAMPLE.splitlines()[0] + "\n" + lines)
    assert found == ["dnsmasq слушает *:53 — нет в allowlist"]


# ── hardcoded_tokens ──────────────────────────────────────────────────────────

FAKE_ANT = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0"          # 20 символов хвоста
FAKE_TG = "1234567890:AA" + "x" * 33


def test_token_scan_finds_planted_without_leaking_value(tmp_path):
    _mk(tmp_path / "bad.py", 0o644, f'KEY = "{FAKE_ANT}"\n'.encode())
    found = ss._hardcoded_tokens(tmp_path)
    assert len(found) == 1
    assert found[0].startswith("bad.py:1") and "anthropic" in found[0]
    # трипвайр: значение НЕ попадает в находку
    assert FAKE_ANT not in found[0]


def test_token_scan_telegram_and_clean_files(tmp_path):
    _mk(tmp_path / "job.sh", 0o644, f"curl -H '{FAKE_TG}'\n".encode())
    _mk(tmp_path / "clean.py", 0o644, b"x = 'sk-ant-... placeholder in docs'\n")
    found = ss._hardcoded_tokens(tmp_path)
    assert len(found) == 1 and "telegram" in found[0]


def test_token_scan_skips_git_and_foreign_suffixes(tmp_path):
    _mk(tmp_path / ".git" / "blob.py", 0o644, FAKE_ANT.encode())
    _mk(tmp_path / "notes.md", 0o644, FAKE_ANT.encode())
    assert ss._hardcoded_tokens(tmp_path) == []


# ── pip_audit: читатель weekly-файла (SEC-19) ─────────────────────────────────

def _write_audit(repo, *, age_s=0.0, status="ok", vulns=(), error=None):
    d = repo / "logs"
    d.mkdir(exist_ok=True)
    (d / "pip_audit_latest.json").write_text(json.dumps({
        "generated": time.time() - age_s,
        "status": status, "vulns": list(vulns), "error": error,
    }))


def test_pip_audit_missing_file_is_loud(tmp_path):
    found = ss._pip_audit(tmp_path)
    assert len(found) == 1 and "ещё не запускался" in found[0]


def test_pip_audit_stale_file_means_dead_job(tmp_path):
    _write_audit(tmp_path, age_s=9 * 86400)
    found = ss._pip_audit(tmp_path)
    assert len(found) == 1 and "мёртв" in found[0]


def test_pip_audit_vulns_formatted_allowlist_respected(tmp_path, monkeypatch):
    vulns = [
        {"name": "pillow", "version": "1.0", "id": "GHSA-xxxx", "fix_versions": ["1.1"]},
        {"name": "requests", "version": "2.0", "id": "GHSA-mute", "fix_versions": []},
    ]
    _write_audit(tmp_path, status="vulns", vulns=vulns)
    monkeypatch.setattr(ss, "_PIP_AUDIT_ALLOW", frozenset({"GHSA-mute"}))
    found = ss._pip_audit(tmp_path)
    assert len(found) == 1
    assert "pillow 1.0" in found[0] and "GHSA-xxxx" in found[0] and "fix: 1.1" in found[0]


def test_pip_audit_error_status_surfaces(tmp_path):
    _write_audit(tmp_path, status="error", error="Timeout while querying OSV")
    found = ss._pip_audit(tmp_path)
    assert len(found) == 1 and "pip-audit упал" in found[0]


# ── tailscale_exposure (SEC-20): live ↔ канон infra_config ────────────────────

_TS_CANON = json.dumps({
    "TCP": {"443": {"HTTPS": True}, "10000": {"HTTPS": True}},
    "AllowFunnel": {"studio.ts.net:10000": True},
    "Web": {
        "studio.ts.net:443": {"Handlers": {
            "/mm": {"Proxy": "http://127.0.0.1:5001/mm"},
        }},
        "studio.ts.net:10000": {"Handlers": {
            "/": {"Proxy": "http://127.0.0.1:9001"},
        }},
    },
})


def test_tailscale_canon_state_clean():
    assert ss._parse_tailscale(_TS_CANON) == []


def test_tailscale_funnel_on_443_screams():
    bad = json.loads(_TS_CANON)
    bad["AllowFunnel"]["studio.ts.net:443"] = True
    found = ss._parse_tailscale(json.dumps(bad))
    assert any("ПУБЛИЧНО торчит порт :443" in f for f in found)


def test_tailscale_funnel_off_10000_screams():
    bad = json.loads(_TS_CANON)
    bad["AllowFunnel"] = {}
    found = ss._parse_tailscale(json.dumps(bad))
    assert any("Funnel :10000 выключен" in f for f in found)


def test_tailscale_public_surface_strict():
    bad = json.loads(_TS_CANON)
    bad["Web"]["studio.ts.net:10000"]["Handlers"]["/admin"] = {"Proxy": "http://127.0.0.1:9001"}
    found = ss._parse_tailscale(json.dumps(bad))
    assert any("публичная поверхность :10000" in f for f in found)


def test_tailscale_serve_backend_outside_canon():
    bad = json.loads(_TS_CANON)
    bad["Web"]["studio.ts.net:443"]["Handlers"]["/x"] = {"Proxy": "http://127.0.0.1:8000"}  # 8000 убит TD-09
    bad["Web"]["studio.ts.net:443"]["Handlers"]["/y"] = {"Proxy": "http://10.0.0.5:5001"}   # не-loopback
    found = ss._parse_tailscale(json.dumps(bad))
    assert sum("бекенд вне канона" in f for f in found) == 2


def test_tailscale_unexpected_endpoint_and_garbage():
    bad = json.loads(_TS_CANON)
    bad["Web"]["studio.ts.net:8444"] = {"Handlers": {}}  # 8443 с 21.09 в каноне (сосед); чужой порт — 8444
    found = ss._parse_tailscale(json.dumps(bad))
    assert any("неожиданный Serve-эндпоинт" in f for f in found)
    # нераспарсили = красный, не зелёный
    assert any("датчик слеп" in f for f in ss._parse_tailscale("not json at all"))


# ── публичная функция: никогда не бросает ─────────────────────────────────────

@pytest.mark.host_only
def test_collect_never_raises_reports_broken_category(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("lsof missing")
    monkeypatch.setattr(subprocess, "run", boom)
    findings = ss.collect_security_findings(home=tmp_path, repo=tmp_path)
    assert any("не отработал" in f for f in findings["listen_ports"])
    assert any("не отработал" in f for f in findings["tailscale_exposure"])
    # остальные категории живы
    assert findings["db_perms"] == [] and findings["hardcoded_tokens"] == []


def test_collect_clean_env_all_empty(tmp_path):
    _write_audit(tmp_path)  # свежий чистый аудит
    findings = ss.collect_security_findings(
        home=tmp_path, repo=tmp_path,
        lsof_text="COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
        ts_json=_TS_CANON,
    )
    assert findings == {
        "db_perms": [], "secrets_perms": [], "listen_ports": [],
        "hardcoded_tokens": [], "pip_audit": [], "tailscale_exposure": [],
    }


# ── доставка: security:* проходит mute-list триажа ────────────────────────────

def test_security_warns_not_muted_by_triage():
    out = classify_warnings([
        ["security:db_perms", "…snap.db perms 644 (канон 600)"],
        ["security:listen_ports", "evil слушает *:2222 — нет в allowlist"],
    ])
    assert len(out) == 2 and all("security:" in o for o in out)


# ── рекурсия сенсора прав (2026-09-02) ───────────────────────────────────────

def test_secrets_perms_walks_subdirectories(tmp_path):
    """RED-first: подкаталог с 644-файлом был слепым пятном (замер: 12 файлов в _env_cache)."""
    import os
    from security_sensors import _secrets_perms
    d = tmp_path / ".health_secrets"; d.mkdir(mode=0o700)
    (d / "token").write_text("x"); os.chmod(d / "token", 0o600)
    sub = d / "_cache"; sub.mkdir(mode=0o755)
    leak = sub / "weather.json"; leak.write_text("{}"); os.chmod(leak, 0o644)
    found = _secrets_perms(tmp_path)
    assert any("weather.json" in f for f in found), "файл в подкаталоге обязан быть виден"
    assert any("_cache" in f and "700" in f for f in found), "права подкаталога тоже канон"


def test_secrets_perms_green_when_all_tight(tmp_path):
    """Негативный контроль: сенсор не краснеет на правильном каталоге."""
    import os
    from security_sensors import _secrets_perms
    d = tmp_path / ".health_secrets"; d.mkdir(mode=0o700)
    (d / "token").write_text("x"); os.chmod(d / "token", 0o600)
    sub = d / "sub"; sub.mkdir(mode=0o700)
    f = sub / "inner"; f.write_text("x"); os.chmod(f, 0o600)
    assert _secrets_perms(tmp_path) == []
