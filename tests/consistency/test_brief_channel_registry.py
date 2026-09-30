"""Каждый канал утреннего брифа обязан назвать свой подавитель повтора.

Множество каналов ВЫЧИСЛЯЕТСЯ обходом AST `gp_agent.generate_daily_report`, а не
описывается словами (§18: датчик, объявляющий периметр, обязан его считать). Равенство
с реестром `gp_agent.BRIEF_CHANNELS` требуется в ОБЕ стороны (§17): незаявленный канал
в коде — красное, осиротевшая запись в реестре — тоже.

Зачем это существует. Анти-повтор в брифе строился ад-хок: локация получила watermark,
«кольцо снято» — streak, гены — скраб и валидатор, а заметки из чата не получили ничего
и трое суток подряд (02–04.08) пересказывали владельцу его же слова. Гейт карточек этого
не видел и не мог — заметки едут в промпт мимо него. Реестр не запрещает завести канал
без подавителя; он запрещает завести его НЕ ЗАМЕТИВ, что подавителя нет.

Граница вслух: сторож доказывает, что вопрос задан и ответ записан, а НЕ что подавитель
работает. Это тот же класс, что вердикт одноразовости (§15) — качество ответа машинно
не проверяется.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

import gp_agent

pytestmark = pytest.mark.consistency

SRC = pathlib.Path(gp_agent.__file__)
MARKER = re.compile(r"^\s*#\s*channel:\s*([a-z_]+)\s*$")


def _touches_context_lines(node) -> bool:
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("append", "extend")
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "context_lines"):
            return True
    return False


def _channel_blocks(src: str | None = None):
    """Каналы, ВЫЧИСЛЕННЫЕ из кода: каждый оператор верхнего уровня тела
    generate_daily_report, который (транзитивно) дописывает в context_lines.
    Возвращает [(номер_строки, id_канала_или_None)]."""
    src = src if src is not None else SRC.read_text()
    lines = src.splitlines()
    out = []
    for fn in ast.walk(ast.parse(src)):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == "generate_daily_report"):
            continue
        for st in fn.body:
            if not _touches_context_lines(st):
                continue
            cid = None
            i = st.lineno - 2                      # строка НАД оператором
            while i >= 0 and not lines[i].strip():
                i -= 1
            if i >= 0:
                m = MARKER.match(lines[i])
                cid = m.group(1) if m else None
            out.append((st.lineno, cid))
    return out


def test_every_channel_is_marked():
    """Ни один источник контента не попадает в бриф безымянным."""
    blocks = _channel_blocks()
    assert blocks, "каналы не найдены — сломан обход, а не код"
    unnamed = [ln for ln, cid in blocks if cid is None]
    assert not unnamed, (
        f"каналы без маркера `# channel: <id>` на строках {unnamed}. "
        "Поставь маркер над блоком и заведи запись в gp_agent.BRIEF_CHANNELS, "
        "назвав подавитель повтора (или честное «нет: <почему не нужен>»). "
        "Маршрут и выбор подавителя: docs/how-to/add_brief_channel.md")


def test_registry_matches_code_both_ways():
    """Равенство множеств в обе стороны: новый канал → красное, мёртвая запись → красное."""
    in_code = {cid for _ln, cid in _channel_blocks() if cid}
    declared = set(gp_agent.BRIEF_CHANNELS)
    assert in_code == declared, (
        f"в коде без реестра: {sorted(in_code - declared)}; "
        f"в реестре без кода: {sorted(declared - in_code)}. "
        "Маршрут: docs/how-to/add_brief_channel.md")


def test_every_channel_declares_a_suppressor():
    """Пустая строка не ответ. «нет» — законный ответ, но обязан нести причину."""
    for cid, s in gp_agent.BRIEF_CHANNELS.items():
        assert s and s.strip(), f"канал {cid} без объявления подавителя"
        if s.startswith("нет"):
            assert len(s) > 20, f"канал {cid}: «нет» без причины — так дыра выглядит нормой"


def test_no_duplicate_channel_ids():
    """Один блок — один id: два блока под общим именем скрыли бы канал от учёта."""
    ids = [cid for _ln, cid in _channel_blocks() if cid]
    assert len(ids) == len(set(ids)), f"повторяющиеся id: {sorted(ids)}"


def test_negative_control_detector_sees_an_unmarked_channel(tmp_path):
    """ИСПОЛНЕННЫЙ негативный контроль: убираем маркер у одного канала в КОПИИ исходника —
    детектор обязан покраснеть. Без этого зелёный test_every_channel_is_marked неотличим
    от зелёного по совпадению: он прошёл бы и на сломанном обходе, который просто ничего
    не находит (§20).

    Правим копию в tmp, не рабочее дерево: тест, который чинит исходник обратно, при
    падении оставил бы репозиторий сломанным.
    """
    src = SRC.read_text()
    broken = src.replace("    # channel: chat_notes\n", "", 1)
    assert broken != src, "маркер не найден — контроль негоден, менять нечего"

    blocks = _channel_blocks(broken)
    unnamed = [ln for ln, cid in blocks if cid is None]
    assert unnamed, "детектор не заметил канал без маркера — как оракул он ничего не стоит"
    assert "chat_notes" not in {cid for _ln, cid in blocks if cid}
