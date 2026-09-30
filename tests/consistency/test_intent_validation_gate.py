"""tests/consistency/test_intent_validation_gate.py — сторож замысла.

Проверяет, что записи в subsystem_intent.yaml СВЯЗАНЫ с реальностью и что
СМЫСЛ кода не разошёлся с утверждениями:

  1. code_anchors (файл::символ) существуют;
  2. головной модуль ссылается назад тегом `# INTENT: <id>`;
  3. тёплая страница на месте и не пустая;
  4. invariants[].check — смысловые маркеры (present/absent) держатся.

check ловит то, чего не ловит «символ жив»: код меняет смысл под тем же именем
(напр. _bh_threshold из Хохберга в Иекутиели → present 'q * rank / m' краснеет).
Это check, НЕ test (RST): стережёт связь и заявленное состояние, но не доказывает
правоту утверждения — смысловая правота остаётся за человеком.

Параметризован по ВСЕМ записям реестра — новая подсистема покрывается сама,
без нового теста (против pesticide paradox). Детерминированный, без LLM и сети.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

import intent_registry as ir

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "subsystem_intent.yaml"


def _registry() -> list[dict]:
    if not REGISTRY.exists():
        pytest.fail(f"реестр замысла {REGISTRY.name} отсутствует")
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or []


_ENTRIES = _registry()
_IDS = [e["id"] for e in _ENTRIES]
_CHECKS = [
    pytest.param(e["id"], inv, id=f"{e['id']}::{inv['id']}")
    for e in _ENTRIES
    for inv in e.get("invariants", [])
    if inv.get("check")
]


def _entry(entry_id: str) -> dict:
    for e in _ENTRIES:
        if e["id"] == entry_id:
            return e
    pytest.fail(f"нет записи id={entry_id!r}")


def _проверить_якорь(entry_id: str, anchor: str, root: Path = None) -> None:
    """Якорь жив? Для Python — символ, для shell — файл плюс право на запуск.

    ПОЧЕМУ ЯКОРЬ БЫВАЕТ SHELL (13.09.2026, найдено попыткой внести запись).
    Гейт умел только `файл.py::символ`, и это молча запрещало реестру описывать
    подсистемы, носители которых — скрипты: хуки, нити (thread_start/finish),
    установщик хуков. Такая подсистема существовала, а записи о ней быть не
    могло — то есть замысел терялся ровно там, где механизм самый хрупкий
    (shell без тестов, запускаемый git'ом). Это не послабление гейта: у shell
    просто другой признак жизни.

    У скрипта с функциями осмысленно спрашивать `файл.sh::имя` — bash объявляет
    функции как `имя()` или `function имя`. У скрипта без функций (весь код
    верхнего уровня — так устроены обе нити) символа нет, и вторым условием
    берётся ПРАВО НА ЗАПУСК: скрипт, потерявший +x, мёртв так же надёжно, как
    исчезнувшая функция, и этот класс здесь уже стрелял (см.
    tests/consistency/test_git_hooks_executable.py: `cp` теряет бит).
    """
    root = ROOT if root is None else root
    fname, _, symbol = anchor.partition("::")
    f = root / fname
    assert f.exists(), f"[{entry_id}] якорь {anchor}: файла {fname} нет"
    src = f.read_text(encoding="utf-8")

    if fname.endswith((".sh", ".bash")) or (not symbol and not fname.endswith(".py")):
        if symbol:
            assert re.search(
                rf"^\s*(?:function\s+{re.escape(symbol)}\b|{re.escape(symbol)}\s*\(\s*\))",
                src, re.M), (
                f"[{entry_id}] якорь {anchor}: функции {symbol!r} в скрипте нет. "
                f"Код изменился — сверь замысел (реестр + docs/explanation).")
        else:
            assert os.access(f, os.X_OK), (
                f"[{entry_id}] якорь {anchor}: скрипт потерял право на запуск. "
                f"Мёртв так же, как исчезнувший символ, но тихо — git просто не "
                f"позовёт его.")
        return

    assert symbol, (
        f"[{entry_id}] якорь {anchor}: у Python-якоря обязан быть символ "
        f"(файл.py::имя) — без него гейт проверяет лишь существование файла.")
    assert re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b", src, re.M), (
        f"[{entry_id}] якорь {anchor}: символ {symbol!r} не найден. "
        f"Код изменился — сверь замысел (реестр + docs/explanation)."
    )


@pytest.mark.parametrize("entry_id", _IDS)
def test_code_anchors_exist(entry_id):
    e = _entry(entry_id)
    for anchor in e["code_anchors"]:
        _проверить_якорь(entry_id, anchor)


@pytest.mark.parametrize("entry_id", _IDS)
def test_backlink_in_head_module(entry_id):
    e = _entry(entry_id)
    head = e["code_anchors"][0].split("::")[0]
    src = (ROOT / head).read_text(encoding="utf-8")
    assert f"INTENT: {entry_id}" in src, (
        f"[{entry_id}] в {head} нет тега `# INTENT: {entry_id}` — код не ссылается на замысел."
    )


@pytest.mark.parametrize("entry_id", _IDS)
def test_explanation_page_present(entry_id):
    e = _entry(entry_id)
    page = ROOT / e["explanation"]
    assert page.exists(), f"[{entry_id}] тёплый слой {e['explanation']} отсутствует"
    assert page.stat().st_size > 400, f"[{entry_id}] {e['explanation']} подозрительно пуст"


@pytest.mark.parametrize("entry_id", _IDS)
def test_invariant_status_valid(entry_id):
    e = _entry(entry_id)
    for inv in e.get("invariants", []):
        assert inv.get("status") in {
            "holds", "doc_drift", "designed_not_built", "open",
        }, f"[{entry_id}] инвариант {inv.get('id')!r}: неизвестный/пустой status"


@pytest.mark.parametrize("entry_id,inv", _CHECKS)
def test_invariant_marker(entry_id, inv):
    chk = inv["check"]
    f = ROOT / chk["file"]
    assert f.exists(), f"[{entry_id}::{inv['id']}] файл {chk['file']} отсутствует"
    src = f.read_text(encoding="utf-8")
    for needle in chk.get("present", []):
        assert needle in src, (
            f"[{entry_id}::{inv['id']}] нет маркера {needle!r} в {chk['file']}. "
            f"Утверждение: «{inv['claim']}». Код разошёлся с замыслом — сверь."
        )
    for needle in chk.get("absent", []):
        assert needle not in src, (
            f"[{entry_id}::{inv['id']}] появился {needle!r} в {chk['file']}. "
            f"Утверждение: «{inv['claim']}». Код разошёлся с замыслом — сверь."
        )


# ── Слой замысел↔тёплая-страница (staleness + status-honesty) ──────────────────
# Первичка — запись реестра, реплика — docs/explanation/<id>.md. Проверяем, что
# реплика выведена из ТЕКУЩЕЙ первички и не «отмывает» невыполненные инварианты.

_NON_HOLDS_IDS = [e["id"] for e in _ENTRIES if ir.non_holds_invariants(e)]
_LAUNDER = [
    pytest.param(e["id"], inv, id=f"{e['id']}::{inv['id']}")
    for e in _ENTRIES
    for inv in ir.non_holds_invariants(e)
    if (inv.get("warm_guard") or {}).get("absent")
]


@pytest.mark.parametrize("entry_id", _IDS)
def test_page_provenance_fresh(entry_id):
    """ЖЁСТКИЙ: тёплая страница выведена из ТЕКУЩЕЙ записи реестра.
    Красный = смысловые поля записи (первичка) уехали, страница (реплика)
    отстала — staleness. Лечение: doc_agent.py --regen-intent <id> → ревью diff."""
    e = _entry(entry_id)
    page = (ROOT / e["explanation"]).read_text(encoding="utf-8")
    got = ir.page_provenance(page)
    assert got is not None, (
        f"[{entry_id}] в {e['explanation']} нет метки intent-provenance — "
        f"страница не заявляет, из какой версии реестра выведена."
    )
    assert not ir.provenance_stale(e, page), (
        f"[{entry_id}] провенанс страницы {got} ≠ реестр {ir.entry_provenance(e)}. "
        f"Перегенери: doc_agent.py --regen-intent {entry_id}, потом сверь diff."
    )


@pytest.mark.parametrize("entry_id", _NON_HOLDS_IDS)
def test_limits_section_present(entry_id):
    """ЖЁСТКИЙ: есть инвариант status!=holds → тёплая страница ОБЯЗАНА иметь
    секцию про пределы (иначе гап тихо исчезает со страницы — H2 через умолчание)."""
    e = _entry(entry_id)
    page = (ROOT / e["explanation"]).read_text(encoding="utf-8").lower()
    assert "предел" in page, (
        f"[{entry_id}] нет секции про пределы, а есть непостроенные/спорные "
        f"инварианты — честный слой обязан их назвать, а не умолчать."
    )


@pytest.mark.parametrize("entry_id,inv", _LAUNDER)
def test_status_not_laundered(entry_id, inv):
    """ЖЁСТКИЙ (absent): инвариант status!=holds не подан утвердительно, как
    работающий (H2). Ловит только перечисленные формы — полнота за ревьюером."""
    e = _entry(entry_id)
    page = (ROOT / e["explanation"]).read_text(encoding="utf-8")
    mine = [h for h in ir.status_laundering(e, page) if h.startswith(inv["id"] + ":")]
    assert not mine, (
        f"[{entry_id}] отмывание статуса: {', '.join(mine)}. "
        f"Инвариант «{inv['claim']}» (status={inv['status']}) подан как работающий."
    )


# ── проба на сам гейт: shell-ветка обязана КРАСНЕТЬ ──────────────────────────
# Расширение гейта под shell (13.09) — это правка СУДЬИ. Судья, расширенный без
# пробы, проверяется только на данных, которые уже зелёные, и незаметно
# превращается в «файл существует». Пробы держат обе стороны: что живой якорь
# проходит и что мёртвый краснеет. Синтетический корень, без сети и LLM.

def _скелет(tmp: Path, имя: str, текст: str, исполняемый: bool) -> Path:
    f = tmp / имя
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(текст, encoding="utf-8")
    f.chmod(0o755 if исполняемый else 0o644)
    return f


def test_проба_shell_якорь_без_символа_живёт_правом_на_запуск(tmp_path):
    _скелет(tmp_path, "s/live.sh", "#!/bin/bash\necho ok\n", исполняемый=True)
    _проверить_якорь("проба", "s/live.sh", root=tmp_path)   # не падает


def test_проба_shell_потерявший_x_краснеет(tmp_path):
    """Ровно тот отказ, который `cp` устраивает молча (О-14 класс)."""
    _скелет(tmp_path, "s/dead.sh", "#!/bin/bash\necho ok\n", исполняемый=False)
    with pytest.raises(AssertionError, match="право на запуск"):
        _проверить_якорь("проба", "s/dead.sh", root=tmp_path)


def test_проба_shell_функция_найдена_и_пропавшая_краснеет(tmp_path):
    _скелет(tmp_path, "s/f.sh", "#!/bin/bash\nдоставить() {\n  echo hi\n}\n",
            исполняемый=True)
    _проверить_якорь("проба", "s/f.sh::доставить", root=tmp_path)
    with pytest.raises(AssertionError, match="функции"):
        _проверить_якорь("проба", "s/f.sh::исчезла", root=tmp_path)


def test_проба_python_якорь_без_символа_запрещён(tmp_path):
    """Послабление не должно протечь на Python: там символ обязателен.

    Иначе расширение под shell дало бы дешёвый обход — `mod.py` без `::`
    проходил бы как «файл есть», и смысловая связь замысла с кодом порвалась
    бы там, где она и была главной.
    """
    _скелет(tmp_path, "m.py", "def f():\n    pass\n", исполняемый=False)
    with pytest.raises(AssertionError, match="обязан быть символ"):
        _проверить_якорь("проба", "m.py", root=tmp_path)
