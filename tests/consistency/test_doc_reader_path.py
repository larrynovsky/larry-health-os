"""Сторож пути читателя: у страницы должен быть вход, а не только файл.

Класс, который лечим: страница пишется в момент работы над предметом, а вход
на неё не заводится — и она становится недостижима ниоткуда, кроме листинга
каталога. Замер 2026-08-04: из 104 страниц вне архива 31 недостижима, и ТРИ
из них родились за предыдущие трое суток (одна — `docs/explanation/llm_egress.md`,
её автор написал эту же сессию). Это не хвост легаси, это действующий класс.

Цена не косметическая. Шесть недостижимых страниц — аварийные how-to («пришёл
алерт X → делай так»): человек в момент инцидента листингом каталога не ходит,
он смотрит в текст алерта. Инструкция, которую не найти в минуту, когда она
нужна, — это мёртвая рельса доставки того же класса, что `run_triage` внутри
чужого расписания (2026-07-26).

ЧТО СЧИТАЕТСЯ ВХОДОМ (корень):
  · код (`*.py`, `*.sh`, `*.plist`) называет страницу ПУТЁМ — так читатель
    приходит по событию: текст алерта, докстринг модуля, сообщение исключения;
  · read-first документ (`CLAUDE.md`, `ARCH_SNAPSHOT.md`, … см. `_ROOTS_DOCS`)
    или реестр замысла — так читатель приходит, когда берётся за тему.
Достижимость ТРАНЗИТИВНА: страница-проводник передаёт вход дальше по ссылке.

ЧТО ВХОДОМ НЕ СЧИТАЕТСЯ:
  · листинг каталога — `lab_pipeline.md` рядом с `lab_review_queue.md` и
    `add_clinical_threshold.md` неразличимы по имени в момент, когда горит;
  · упоминание голого имени без пути — в проекте есть тёзки (страница
    `how-to/adjudicate_quarantine.md` и скрипт `scripts/adjudicate_quarantine.py`),
    и грепу они неотличимы;
  · архив (`docs/handoff/**`) — снимок нити неизменяем и сам не вход;
    его читают по Handoff ID. Страница, достижимая ТОЛЬКО из снимка, —
    недостижима (решение владельца 2026-08-04).

ЧЕГО СТОРОЖ НЕ ДОКАЗЫВАЕТ (предикат односторонний, ср. §17):
отсутствие входа доказывает недостижимость; наличие входа НЕ доказывает, что
страницу читают. Это прокси, и он назван прокси намеренно.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from doc_translation import strip_switch

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "doc_inventory.yaml"

# Входы-документы: то, что читают до задачи (CLAUDE.md § Read-first) + реестры.
_ROOTS_DOCS = {
    "CLAUDE.md", "ARCH_SNAPSHOT.md", "TESTING_CONTRACTS.md", "SECURITY.md",
    "README.md", "TEST_ARCHITECTURE.md", "USE_CASES.md", "ROADMAP.md",
    "CONSTITUTION_RULES.md", "subsystem_intent.yaml",
    # Тексты сообщений бота переехали из кода в таблицы строк (i18n, 28.09): аварийный
    # how-to, названный в тексте алерта, называется теперь здесь.
    "methodology/i18n/ru.yaml", "methodology/i18n/en.yaml",
}
# `doc_inventory.yaml` корнем НЕ считается, хотя и читается до задачи: список
# `reader_path.frozen` живёт в нём, и как корень он объявлял бы входом ровно те
# страницы, которые в нём заморожены за отсутствие входа. Второй за час случай
# «механизм кормит сам себя» (первый — сам сторож в корнях); оба ловятся только
# прогоном, чтением кода не видны.
_ROOT_CODE_SUFFIX = (".py", ".sh", ".plist")
# Архив и планы-снимки: о прошлом, входом не работают.
_ARCHIVE = ("docs/handoff/", "docs/DIATAXIS_WORKPLAN")


def _tracked(*globs: str) -> list[str]:
    """Индекс + ещё не добавленные файлы — иначе сторож судит на коммит позже.

    Тот же `--others`, что в `test_doc_section_refs._tracked` и в
    `affected_tests._staged`: новая страница почти всегда untracked в момент,
    когда её вход ещё не заведён, — то есть ровно в момент, который ловим.

    Через `git_facts` (2026-08-10): в песочнице репозитория нет по построению,
    и прямой вызов падал там rc=128 — красное от среды, а не от кода.
    """
    import git_facts
    return git_facts.tracked(*globs)


def _read(rel: str) -> str:
    try:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        # Root translations are outside the docs/ graph; retain switches within that graph.
        return strip_switch(text) if "/" not in rel and rel.endswith(".md") else text
    except OSError:
        return ""


def _addresses(page: str) -> tuple[str, ...]:
    """Адресные формы страницы: путь целиком, путь без `docs/`, md-ссылка, вики."""
    base = os.path.basename(page)
    return (page, page[len("docs/"):], f"({base})", f"]({base}", f"[[{base[:-3]}]]")


def _links_to(text: str, page: str) -> bool:
    return any(a in text for a in _addresses(page))


def reachable(pages: list[str], roots: dict[str, str],
              texts: dict[str, str]) -> set[str]:
    """Транзитивное замыкание достижимости от корней. Чистая функция — её
    и проверяет `test_closure_walks_through_a_relay` на синтетике."""
    found = {p for p in pages if any(_links_to(t, p) for t in roots.values())}
    frontier = list(found)
    while frontier:
        cur = frontier.pop()
        relay = texts.get(cur, "")
        for p in pages:
            if p != cur and p not in found and _links_to(relay, p):
                found.add(p)
                frontier.append(p)
    return found


def _frozen() -> tuple[set[str], str]:
    data = yaml.safe_load(INVENTORY.read_text(encoding="utf-8")) or {}
    block = data.get("reader_path") or {}
    return set(block.get("frozen") or []), str(block.get("measured") or "")


def _measure() -> tuple[set[str], set[str]]:
    """→ (недостижимые страницы, замороженные-но-уже-достижимые)."""
    pages = [f for f in _tracked("docs/**/*.md")
             if not any(f.startswith(a) for a in _ARCHIVE)]
    # Сам сторож цитирует страницы как МАТЕРИАЛ (примеры в докстринге) — если
    # он останется корнем, то назначит входом собственный текст. Поймано при
    # первом же прогоне 2026-08-04: `llm_egress.md` и `adjudicate_quarantine.md`
    # позеленели от упоминания здесь, а `llm_guard_blocked.md` — через первую из
    # них как проводника. Тот же класс само-зелёного, что у детектора LLM-трактов
    # по подстроке (он объявил покрытым сам integrity_tests, 2026-08-03).
    _self = str(Path(__file__).relative_to(ROOT))
    roots = {f: _read(f) for f in _tracked(*(f"*{s}" for s in _ROOT_CODE_SUFFIX),
                                           "**/*.py", "**/*.sh") if f != _self}
    roots.update({f: _read(f) for f in _ROOTS_DOCS if (ROOT / f).exists()})
    texts = {p: _read(p) for p in pages}

    ok = reachable(pages, roots, texts)
    frozen, _ = _frozen()
    unreached = set(pages) - ok
    return unreached - frozen, frozen & ok


@pytest.mark.owner_data
def test_new_page_has_a_reader_path():
    """Новая страница обязана родиться со входом. Замороженный хвост — в
    `doc_inventory.yaml::reader_path.frozen`, он снимается по касанию темы."""
    fresh, _ = _measure()
    assert not fresh, (
        "страницы без входа читателя (ни код, ни read-first не называют их ПУТЁМ):\n  "
        + "\n  ".join(sorted(fresh))
        + "\n\nЗаведи вход, а не ссылку ради ссылки: аварийный how-to называется в "
          "ТЕКСТЕ алерта/исключения, explanation — в докстринге своего носителя или "
          "в CLAUDE.md. Если страница о прошлом — её дом CHANGELOG/архив, не docs/.")


def test_frozen_list_does_not_rot():
    """Ратчет только вниз: получила вход — уходит из заморозки в тот же коммит.

    Без этого список замерзает навсегда и через полгода описывает мир, которого
    нет, — ровно то, за что вчера удалён BLUEPRINT.md."""
    _, healed = _measure()
    assert not healed, (
        "эти страницы уже достижимы, но числятся в reader_path.frozen — убери их:\n  "
        + "\n  ".join(sorted(healed)))


def test_frozen_paths_are_alive():
    """Строка `frozen`, указывающая в пустоту, не краснеет ни одним другим тестом.

    `_measure()` отсекает архив и несуществующие файлы ДО сравнения с `frozen`:
    такая страница не попадает ни в `unreached`, ни в `healed`. То есть после
    переноса страницы в `plans/` (или удаления) её строка осталась бы здесь
    навсегда, описывая мир, которого нет, — ровно то, за что 2026-08-03 удалён
    BLUEPRINT.md. Пойман при переносе `wave9_test_infrastructure_plan.md`
    в `plans/` 2026-08-04: дыру открывает ЛЮБОЙ будущий перенос, не только этот.
    """
    frozen, _ = _frozen()
    gone = sorted(p for p in frozen if not (ROOT / p).exists())
    archived = sorted(p for p in frozen if any(p.startswith(a) for a in _ARCHIVE))
    assert not gone and not archived, (
        "в reader_path.frozen строки, которые сторож не судит и потому никогда "
        "не покраснеют:\n  "
        + "\n  ".join(f"{p} — файла нет" for p in gone)
        + "\n  ".join(f"{p} — в архиве" for p in archived)
        + "\n\nУбери строку тем же коммитом, что переносит или удаляет страницу.")


def test_frozen_block_carries_measurement_date():
    """§18: утверждение о мире вне своего носителя несёт дату замера."""
    frozen, measured = _frozen()
    assert measured, "doc_inventory.yaml::reader_path.measured пуст — замер без даты"
    if frozen:
        assert len(measured) == 10 and measured[4] == measured[7] == "-", \
            f"reader_path.measured — не дата вида YYYY-MM-DD: {measured!r}"


def test_closure_walks_through_a_relay():
    """Оракул самого механизма: вход передаётся по цепочке, но не из архива.

    Красит: если замыкание перестанет ходить дальше первого шага (тогда
    достижимых окажется меньше и хвост вырастет молча) или начнёт считать
    проводником страницу, до которой само не дошло."""
    pages = ["docs/a.md", "docs/b.md", "docs/c.md"]
    roots = {"m.py": 'HOWTO = "docs/a.md"'}
    texts = {"docs/a.md": "дальше см. [b](b.md)", "docs/b.md": "", "docs/c.md": ""}
    assert reachable(pages, roots, texts) == {"docs/a.md", "docs/b.md"}
    # c ссылается на b, но сам недостижим — проводником быть не может
    texts["docs/c.md"] = "docs/b.md"
    assert "docs/c.md" not in reachable(pages, {}, texts)
