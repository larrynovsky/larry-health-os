"""G7 (решение владельца 01.10): выпуск не ставит тег, пока CodeQL не разобрал этот коммит или
открыты critical/high. Падение = серьёзная находка снова может уехать к людям незамеченной."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("public_mirror", ROOT / "scripts" / "public_mirror.py")
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)

HEAD = "abc1234def"
SCANNED = [{"commit_sha": HEAD, "tool": {"name": "CodeQL"}}]


def _alert(n, sev):
    return {"number": n, "rule": {"security_severity_level": sev}}


def test_unscanned_commit_blocks():
    assert "не разобрал" in pm.codeql_verdict(HEAD, [{"commit_sha": "old", "tool": {"name": "CodeQL"}}], [])


def test_open_high_or_critical_blocks():
    why = pm.codeql_verdict(HEAD, SCANNED, [_alert(35, "critical"), _alert(4, "high"), _alert(3, "medium")])
    import re
    assert re.findall(r"#\d+", why) == ["#4", "#35"]          # medium #3 выпуск не держит


def test_clean_scan_passes():
    assert pm.codeql_verdict(HEAD, SCANNED, [_alert(3, "medium"), _alert(9, None)]) is None


# Решение владельца 01.10: гейт расширен на уязвимые зависимости (Dependabot).
def test_dependabot_high_blocks_medium_does_not():
    why = pm.dependabot_verdict([{"number": 4, "severity": "high", "package": "urllib3"},
                                 {"number": 1, "severity": "medium", "package": "oauthlib"}])
    assert "#4 urllib3" in why and "oauthlib" not in why, why
    assert pm.dependabot_verdict([{"number": 1, "severity": "medium", "package": "x"}]) is None
