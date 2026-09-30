"""Журнал замера видит ОБА канала discovery, не только MCP-тул.

Что стерегут. Инвариант подсистемы `discovery_called_before_build` («зовут ли
discovery до постройки») судится по журналу `~/.project_context_usage.jsonl`. До
14.09 писал в него ровно один вызывающий — `mcp_server`, — а из терминала ту же
функцию зовут командой `python3 -m project_context capabilities`, и она не
оставляла следа. Журнал описывал не привычку звать discovery, а привычку звать её
через MCP.

Цена замерена: из 27 коммитов, вводивших новый судимый `.py` за 51 день журнала,
у 18 нашёлся вызов capabilities в сутки до коммита, у 9 — нет. Каждое «нет»
неразличимо с «звали из терминала», поэтому инвариант по таким данным нельзя
закрыть ни в одну сторону — не «мало материала», а слепой канал.

Тест поведенческий, через ПОДПРОЦЕСС, а не проверкой наличия строки в исходнике:
ровно этот путь и был слепым, и «функция существует» о нём ничего не говорит
(тот же урок, что с мёртвой рельсой триажа — проверяй путь, а не слой).
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_cli_capabilities_leaves_a_trace(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    env = {**dict(__import__("os").environ), "PC_USAGE_LOG": str(log)}
    r = subprocess.run([sys.executable, "-m", "project_context", "capabilities", "lab_results"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
    assert log.exists(), "CLI-вызов discovery не оставил следа в журнале замера"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [x["tool"] for x in rows] == ["project_capabilities"]
    assert rows[0]["arg"] == "lab_results"
    # Корень записан ИМЕНЕМ репозитория, как у MCP-канала: иначе две формы одного
    # поля («health_scripts» и «.») разошлись бы и счёт по корням врал бы молча.
    assert rows[0]["root"] == ROOT.name


def test_build_refuses_a_dead_root():
    """⭐ Мёртвый корень КРИЧИТ, а не отдаёт пустой индекс.

    `os.walk` по несуществующему пути молча возвращает пустоту, и `capabilities`
    отвечает `by_name: []` — ровно тем же, чем при живом репозитории без дублей.
    «Искал и не нашёл» становится неотличимо от «искать было негде», а это и есть
    та тишина, ради которой подсистема существует. Живой случай 14.09: второй
    MCP-сервер смотрел в путь репозитория, переехавшего 13.09.
    """
    from project_context import indexer
    with pytest.raises(FileNotFoundError):
        indexer.build("/nonexistent/root/for/this/test")


def test_build_still_works_on_a_live_root(tmp_path):
    """Позитивный контроль: на существующем корне (пусть и без .py) отказа нет —
    иначе предыдущий тест зеленел бы и на правиле «падать всегда»."""
    from project_context import indexer
    ix = indexer.build(str(tmp_path))
    assert isinstance(ix, dict)


def test_roots_check_кричит_о_мёртвом_адресе_сервера(tmp_path):
    """⭐ Сторож адресов (22.09): мёртвый PROJECT_CONTEXT_ROOT в конфиге Desktop — exit 1
    и имя сервера в stderr; живой — тишина и exit 0; чужой сервер не судится.

    Зачем через ПОДПРОЦЕСС: post-commit зовёт именно CLI, и слепым мог быть путь
    вызова, а не функция. Живой случай: адрес `project-context-mm` исправили 14.09,
    22.09 он снова смотрел в удалённый iCloud-путь, и узнали об этом случайно."""
    live = tmp_path / "живой"; live.mkdir()
    eng = ["-m", "project_context.mcp_server"]
    cfg = {"mcpServers": {
        "pc-dead": {"args": eng, "env": {"PROJECT_CONTEXT_ROOT": str(tmp_path / "нет")}},
        "pc-live": {"args": eng, "env": {"PROJECT_CONTEXT_ROOT": str(live)}},
        "other":   {"args": ["-m", "x"], "env": {"PROJECT_CONTEXT_ROOT": "/нет/и/не/надо"}}}}
    p = tmp_path / "cfg.json"; p.write_text(json.dumps(cfg), encoding="utf-8")

    def run(path):
        return subprocess.run([sys.executable, "-m", "project_context", "roots-check", str(path)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
    r = run(p)
    assert r.returncode == 1 and "pc-dead" in r.stderr
    assert "pc-live" not in r.stderr and "other" not in r.stderr
    del cfg["mcpServers"]["pc-dead"]; p.write_text(json.dumps(cfg), encoding="utf-8")
    r = run(p)                                   # позитивный контроль: не «кричать всегда»
    assert r.returncode == 0 and r.stderr == ""


def test_post_commit_зовёт_сторож_адресов():
    """Носитель: сторож без вызова из хука — детект без доставки."""
    hook = (ROOT / "scripts/git-hooks/post-commit-macbook").read_text(encoding="utf-8")
    assert "project_context roots-check" in hook
