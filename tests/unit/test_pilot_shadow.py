"""Теневой сторож пилота (docker-install, этап 10). Краснеет, если расхождение вердиктов молчит,
если одно и то же расхождение шлётся каждый час, если сбой сравнения проглатывается."""
import sys
import types

import pytest

import pilot_shadow as ps

pytestmark = pytest.mark.unit


@pytest.fixture
def sent(monkeypatch, tmp_path):
    box = []
    monkeypatch.setitem(sys.modules, "notify", types.SimpleNamespace(notify_operator=box.append))
    monkeypatch.setattr(ps, "SHADOW", tmp_path / "health")
    monkeypatch.setenv("HEALTH_SHADOW_CONTAINER", "c")
    monkeypatch.setattr(ps, "frozen_findings", lambda: [])        # копия судится своими тестами ниже
    return box


def test_diff_видит_тревогу_и_правило_только_с_одной_стороны():
    n = {"alerts": [["CA19-9", "urgent", None], ["LDL", "warn", None]],
         "rules": [["trend", "CA19-9", "up", 11.3, 2, "urgent", None]]}
    assert ps.diff(n, n) == []
    got = ps.diff(n, {"alerts": [["LDL", "warn", None]], "rules": n["rules"]})
    assert len(got) == 1 and got[0].startswith("тревога только на хосте") and "CA19-9" in got[0]
    # тревоги совпали, правило разошлось — сторож обязан это видеть (замер 29.09: иначе слеп)
    moved = {"alerts": n["alerts"], "rules": [["trend", "CA19-9", "up", 25.0, 2, "urgent", None]]}
    got = ps.diff(n, moved)
    assert len(got) == 2 and all(g.startswith("правило только") for g in got)


def test_нативная_сторона_пересевает_копию_своим_кодом():
    """Без пересева обе стороны читали бы таблицы, засеянные контейнером, — сравнение пустое по построению."""
    assert ps.NATIVE.startswith("import health_db; health_db.init_db();") and ps.NATIVE.endswith(ps.VERDICT)


def test_расхождение_доходит_сразу_а_повтор_не_чаще_суток(monkeypatch, sent):
    monkeypatch.setattr(ps, "compare", lambda c, d: ["только на хосте: CA19-9 (urgent)"])
    assert ps.main() == 1 and len(sent) == 1 and "расхождений: 1" in sent[0]
    assert ps.main() == 1 and len(sent) == 1                       # тот же час — тишина
    monkeypatch.setattr(ps.time, "time", lambda: 10 ** 12)          # сутки спустя — снова
    assert ps.main() == 1 and len(sent) == 2


def test_совпадение_сбрасывает_антиспам(monkeypatch, sent):
    found = [["только на хосте: CA19-9 (urgent)"]]
    monkeypatch.setattr(ps, "compare", lambda c, d: found[0])
    ps.main()
    found[0] = []
    assert ps.main() == 0 and len(sent) == 1
    found[0] = ["только на хосте: CA19-9 (urgent)"]
    ps.main()
    assert len(sent) == 2                                          # вернулось — пришло сразу


def test_сбой_сравнения_не_молчит(monkeypatch, sent):
    def boom(c, d):
        raise RuntimeError("docker exec: no such container")
    monkeypatch.setattr(ps, "compare", boom)
    assert ps.main() == 1 and "не смог сравнить" in sent[0]
    monkeypatch.delenv("HEALTH_SHADOW_CONTAINER")                   # другой сбой — другой текст, приходит
    assert ps.main() == 1 and "HEALTH_SHADOW_CONTAINER" in sent[-1]


def _frozen(tmp_path, mode=0o400):
    d = tmp_path / "data"
    d.mkdir()
    (d / "health.db").write_bytes(b"x")
    (d / "health.db").chmod(mode)
    return d


def test_нетронутая_копия_молчит_а_запись_права_и_служба_видны(tmp_path):
    """Замер 30.09 12:52: после перезагрузки code-watcher записал сиды в замороженную копию владельца —
    ремонт прав монитора партнёра вернул файлу 600. Каждый из трёх признаков — своя находка."""
    d, stamp = _frozen(tmp_path), tmp_path / "stamp.json"
    kw = dict(stamp=stamp, loaded={"com.larry.health.bot.partner"}, bootout={"com.larry.health.bot"})
    assert ps.frozen_findings(d, **kw) == []                       # первый прогон — замер
    assert ps.frozen_findings(d, **kw) == []                       # ничего не менялось — тишина
    (d / "health.db-wal").write_bytes(b"w")                        # второй писатель оставил журнал
    assert any("health.db-wal изменился" in f for f in ps.frozen_findings(d, **kw))
    (d / "health.db").chmod(0o600)
    assert any("права замороженной базы 600" in f for f in ps.frozen_findings(d, **kw))
    got = ps.frozen_findings(d, stamp=stamp, loaded={"com.larry.health.bot"}, bootout={"com.larry.health.bot"})
    assert any("загружены нативные службы владельца: com.larry.health.bot" in f for f in got)


def test_находка_копии_доходит_до_владельца(monkeypatch, sent):
    monkeypatch.setattr(ps, "compare", lambda c, d: [])
    monkeypatch.setattr(ps, "frozen_findings", lambda: ["права замороженной базы 600, ждём 400"])
    assert ps.main() == 1 and "второй писатель" in sent[0] and "600" in sent[0]
