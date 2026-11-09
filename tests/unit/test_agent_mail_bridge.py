"""Мост почты агентов (scripts/agent_mail_bridge.py) на поддельном почтамте: настоящий post.py не зовётся (§20)."""
import importlib
import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

STUB = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path
    st = Path(os.environ["STUB_STATE"])
    log = st / "calls.jsonl"
    a = sys.argv[1:]
    body = sys.stdin.read() if a[0] == "send" else ""
    with log.open("a") as f:
        f.write(json.dumps({"argv": a, "body": body}) + "\\n")
    if a[0] == "send":
        kv = dict(zip(a[1::2], a[2::2]))
        for r in kv["--to"].split(","):
            print(r, "delivered")
        print("message-id <m1@test>")
    elif a[0] == "inbox":
        print((st / "inbox.txt").read_text() if (st / "inbox.txt").exists() else "", end="")
    elif a[0] == "read":
        print('<письмо номер="%s">тело</письмо>' % a[2])
    elif a[0] == "ack":
        print("принято")
''')


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "post.py").write_text(STUB)
    (infra / "addresses.yaml").write_text(yaml.safe_dump({"agents": [
        {"id": "me-h", "owner": "o1"}, {"id": "pa", "owner": "o1"}, {"id": "other", "owner": "o2"},
        {"id": "me2-h", "owner": "o2"}]}))
    state = tmp_path / "state"
    state.mkdir()
    tenants = tmp_path / "tenants.yaml"
    tenants.write_text(yaml.safe_dump({"tenants": {
        "t1": {"agent": "me-h", "dir": str(tmp_path / "t1")},
        "t2": {"agent": "me2-h", "dir": str(tmp_path / "t2")}}}))
    monkeypatch.setenv("STUB_STATE", str(state))
    monkeypatch.setenv("AGENT_POST_ROOT", str(tmp_path / "post"))
    from scripts import agent_mail_bridge as m
    monkeypatch.setattr(m, "INFRA", infra)
    monkeypatch.setattr(m, "TENANTS", tenants)
    monkeypatch.setattr(m, "PY", sys.executable)
    m.run_once()                                   # заводит каталоги
    monkeypatch.setattr(m, "state", state, raising=False)
    monkeypatch.setattr(m, "root", tmp_path, raising=False)
    return m


def calls(m):
    p = m.state / "calls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def put(m, tenant, rid, req):
    (m.root / tenant / "out" / f"{rid}.json").write_text(json.dumps(req))


RID = "a" * 32


def test_отправитель_ставится_мостом_а_не_заявкой(bridge):
    put(bridge, "t1", RID, {"to": ["pa"], "subject": "s", "body": "привет"})
    bridge.run_once()
    send = [c for c in calls(bridge) if c["argv"][0] == "send"]
    assert len(send) == 1 and send[0]["argv"][send[0]["argv"].index("--from") + 1] == "me-h"
    rec = json.loads((bridge.root / "t1/sent" / f"{RID}.json").read_text())
    assert rec["statuses"] == {"pa": "delivered"} and rec["message_id"] == "<m1@test>" and rec["refused"] is None
    assert not (bridge.root / "t1/out" / f"{RID}.json").exists()


@pytest.mark.parametrize("req,why", [
    ({"to": ["pa"], "from": "pa", "body": "x"}, "from_field"),
    ({"to": ["other"], "body": "x"}, "foreign_owner"),
    ({"to": ["nobody"], "body": "x"}, "unknown_rcpt"),
    ({"to": ["pa"], "body": "x" * 8001}, "too_long"),
    ({"to": [], "body": "x"}, "bad_json"),
])
def test_отказ_моста_до_почтамта(bridge, req, why):
    put(bridge, "t1", RID, req)
    bridge.run_once()
    assert not [c for c in calls(bridge) if c["argv"][0] == "send"], "заявка ушла в почтамт"
    assert json.loads((bridge.root / "t1/sent" / f"{RID}.json").read_text())["refused"] == why


def test_входящее_ложится_в_in_и_ack_только_по_файлу_health(bridge):
    key = "b" * 24
    (bridge.state / "inbox.txt").write_text(f"{key} от pa <m2@test>\n")
    bridge.run_once()
    got = json.loads((bridge.root / "t1/in" / f"{key}.json").read_text())
    assert got["from"] == "pa" and got["message_id"] == "<m2@test>" and "тело" in got["wrapped"]
    assert not [c for c in calls(bridge) if c["argv"][0] == "ack"], "ack до того, как health показал письмо"
    (bridge.root / "t1/ack" / key).touch()
    bridge.run_once()
    assert [c["argv"] for c in calls(bridge) if c["argv"][0] == "ack"] == [["ack", "me-h", key]]
    assert (bridge.root / "t1/acked" / key).exists() and not (bridge.root / "t1/ack" / key).exists()


def test_автоответ_почтамта_подтверждается_мостом_и_не_показывается(bridge):
    key = "d" * 24
    cur = bridge.root / "post/boxes/o1/me-h/cur"
    cur.mkdir(parents=True)
    (cur / "x.eml").write_bytes(b"Message-ID: <m4@test>\r\nSubject: receipt\r\nAuto-Submitted: auto-replied\r\n\r\nok\r\n")
    (bridge.state / "inbox.txt").write_text(f"{key} от pa <m4@test>\n")
    bridge.run_once()
    assert not (bridge.root / "t1/in" / f"{key}.json").exists()
    assert ["ack", "me-h", key] in [c["argv"] for c in calls(bridge)]


def test_тема_берётся_из_файла_письма(bridge):
    key = "e" * 24
    cur = bridge.root / "post/boxes/o1/me-h/cur"
    cur.mkdir(parents=True)
    (cur / "y.eml").write_bytes("Message-ID: <m5@test>\r\nSubject: =?utf-8?b?0YHQvtC9?=\r\n\r\nok\r\n".encode())
    (bridge.state / "inbox.txt").write_text(f"{key} от pa <m5@test>\n")
    bridge.run_once()
    assert json.loads((bridge.root / "t1/in" / f"{key}.json").read_text())["subject"] == "сон"


def test_прочитанное_повторно_не_читается(bridge):
    key = "c" * 24
    (bridge.state / "inbox.txt").write_text(f"{key} от pa <m3@test>\n")
    bridge.run_once()
    bridge.run_once()
    assert len([c for c in calls(bridge) if c["argv"][:1] == ["read"] and c["argv"][1] == "me-h"]) == 1


def test_сбой_книги_не_шлёт_никому(bridge):
    put(bridge, "t1", RID, {"to": ["pa"], "body": "x"})
    (bridge.INFRA / "addresses.yaml").write_text("agents: [")
    with pytest.raises(yaml.YAMLError):
        bridge.run_once()
    assert not [c for c in calls(bridge) if c["argv"][0] == "send"]


def test_сбой_одного_тенанта_не_роняет_другого_и_не_обновляет_его_alive(bridge, monkeypatch):
    t = yaml.safe_load(bridge.TENANTS.read_text())
    t["tenants"]["t1"]["agent"] = "нет-такого"
    bridge.TENANTS.write_text(yaml.safe_dump(t))
    before = (bridge.root / "t1/alive").read_text()
    (bridge.root / "t1/alive").write_text("0")
    out = bridge.run_once()
    assert any("t1: СБОЙ" in x for x in out) and any(x.startswith("t2:") and "СБОЙ" not in x for x in out)
    assert (bridge.root / "t1/alive").read_text() == "0" and before != "0"
