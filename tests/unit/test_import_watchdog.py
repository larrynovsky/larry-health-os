"""Сторож свежести Apple Health (import_watchdog): тревога на протухшую метку — одна за 12 ч,
на свежую — тишина, лимит в тексте — из get_conit_limit. Краснеет, если сторож замолчит на
протухших данных, начнёт спамить или снова зашьёт лимит в текст."""
import pytest

import import_status_db
import import_watchdog
import notify

pytestmark = pytest.mark.unit


@pytest.fixture
def sent(monkeypatch, tmp_path):
    out = []
    monkeypatch.setattr(notify, "notify_operator", lambda msg, fallback=True: out.append(msg) or "telegram")
    monkeypatch.setattr(import_watchdog, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(import_status_db, "get_conit_limit", lambda src: 41.0)
    return out


def _age(monkeypatch, stale, age_h):
    monkeypatch.setattr(import_status_db, "get_import_staleness", lambda src: (stale, age_h))


def test_протухшая_метка_одна_тревога_за_12ч(monkeypatch, sent):
    _age(monkeypatch, True, 50.0)
    t0 = 1_800_000_000.0
    assert import_watchdog.main(now=t0) == 1
    assert import_watchdog.main(now=t0 + 11 * 3600) == 0
    assert import_watchdog.main(now=t0 + 13 * 3600) == 1
    assert len(sent) == 2 and "лимит 41ч" in sent[0] and "30ч" not in sent[0]


def test_свежая_метка_тишина(monkeypatch, sent):
    _age(monkeypatch, False, 2.0)
    assert import_watchdog.main(now=1_800_000_000.0) == 0 and sent == []


def test_битое_состояние_не_глушит_тревогу(monkeypatch, sent, tmp_path):
    (tmp_path / "state.json").write_text("{не json", encoding="utf-8")
    _age(monkeypatch, True, 50.0)
    assert import_watchdog.main(now=1_800_000_000.0) == 1 and len(sent) == 1
