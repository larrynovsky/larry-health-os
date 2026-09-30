#!/usr/bin/env python3.11
"""affected_claims.py — какие §-утверждения свода затронуты этой правкой.

ЗАЧЕМ. Свод описывает поведение файлов, которые живут отдельно от свода. Правка
файла может сделать утверждение о нём ложным в ту же секунду, и §18 требует у
такого утверждения гаситель — счётчик либо дату замера. Замер 2026-08-20 по
четырём признанным дрейфам: лаг между «стало ложным» и «исправлено» — 3–9 дней,
и все четыре нашёл человек, затеявший сверку; ни одного не нашёл датчик. Один из
случаев особенно показателен: живой ЗЕЛЁНЫЙ маркер стерёг соседнее утверждение,
пока проверяемое было ложным пять дней (validation_gate, BH-vs-BY, 21→26.07).
Отсюда форма: не таймер и не проверка живости сторожа, а ПРИЧИННАЯ связь —
правка носителя поднимает вопрос об утверждении в момент коммита.

ЧТО СЧИТАЕТСЯ СВЯЗЬЮ. §-строка объявляет носители явной меткой:

    *Enforcement:* **LIVE** — ... ⟨carriers: backup.sh, backup_studio.sh⟩

Носитель адресуется файлом или файлом с символом внутри него:

    ⟨carriers: project_context/dispgate.py::perimeter_policy⟩

Вторая форма точнее (она переживает переезд функции внутри файла и краснеет при
её переименовании), и отбор по ней идёт по файловой части — git отдаёт пути.

Затронуто = хоть один объявленный носитель есть среди изменённых путей. Признак
один и он явный: подстрочный поиск имён по тексту свода здесь НЕ используется
намеренно — свод называет `pre_commit_check.py` голым именем, а файл живёт в
scripts/git-hooks/, и совпадение по имени дало бы связь, которая рассыпется при
первой же реорганизации каталогов, оставшись тихо-зелёной.

ЧТО КРАСНОЕ, А НЕ МОЛЧАНИЕ (§14: нераспознанный вход — отказ инструмента):
  · метка не закрыта (`⟨carriers:` без `⟩`);
  · метка пуста;
  · объявленный носитель не существует на диске.
Последнее — главный страж от тихой смерти: механизм, чьи адреса протухли,
зеленел бы вечно, занимая место оракула.

ЧЕГО НЕ ЛОВИТ, названо вслух:
  · утверждение, ставшее ложным без правки носителя (смена конфига launchd,
    поведение внешнего сервиса, решение человека);
  · носитель, собранный из переменных или спрятанный за маской;
  · §-строки без метки — их для механизма не существует, и пустое множество
    меток даёт вечно-зелёный молчаливый прогон. Поэтому есть контроль
    полноты в tests/consistency/, а не только характеризация поведения.
  · ИСТИННОСТЬ утверждения. Механизм говорит «носитель тронут, перечитай §»,
    а верно ли утверждение после правки — суждение человека. Обещать больше
    значило бы повторить ошибку heartbeat-вместо-корректности (§14, оговорка).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).parent
SVOD = ROOT / "CLAUDE.md"

# Угловые скобки U+27E8/27E9 — не встречаются в прозе свода и не ломают markdown.
_METKA = re.compile(r"⟨carriers:(?P<body>[^⟩]*)⟩")
_UNCLOSED = re.compile(r"⟨carriers:(?![^⟩]*⟩)")
_SECTION = re.compile(r"\*\*(§\d+[а-яa-z]?)\b")
# Строка, ОБЪЯВЛЯЮЩАЯ живой механизм: именно она обещает поведение соседа.
_LIVE = re.compile(r"\*Enforcement:\*.*\*\*LIVE")


class Claim(NamedTuple):
    line: int          # 1-based номер строки в своде
    section: str       # «§1», либо «L<номер>» если § выше не найден
    carriers: tuple[str, ...]


class Hit(NamedTuple):
    claim: Claim
    carrier: str       # какой именно носитель тронут


def _section_of(lines: list[str], idx: int) -> str:
    """Ближайший §-заголовок ВЫШЕ строки. Enforcement-строки принадлежат § над ними."""
    for j in range(idx, -1, -1):
        m = _SECTION.search(lines[j])
        if m:
            return m.group(1)
    return f"L{idx + 1}"


def parse_claims(text: str) -> tuple[list[Claim], list[str]]:
    """→ (утверждения с метками, ошибки). Непустые ошибки = отказ инструмента,
    а не «меток нет»: молчание на битой метке сделало бы тихим каждый будущий дефект."""
    lines = text.split("\n")
    claims: list[Claim] = []
    errors: list[str] = []
    for i, ln in enumerate(lines):
        if _UNCLOSED.search(ln):
            errors.append(f"L{i + 1}: метка ⟨carriers: не закрыта ⟩")
            continue
        m = _METKA.search(ln)
        if not m:
            continue
        raw = [p.strip() for p in m.group("body").split(",")]
        paths = tuple(p for p in raw if p)
        if not paths:
            errors.append(f"L{i + 1}: метка пуста — носитель не объявлен")
            continue
        claims.append(Claim(i + 1, _section_of(lines, i), paths))
    return claims, errors


def split_carrier(carrier: str) -> tuple[str, str | None]:
    """«path/file.py::symbol» → («path/file.py», «symbol»); без `::` символ None.

    Форма с символом появилась в своде 2026-09-11 (§15, периметр), а механизм её
    не знал и проверял существование файла с именем «...py::symbol» — то есть
    краснел на верной метке и одновременно был СЛЕП для отбора: сравнение с
    изменёнными путями тоже шло по строке с символом и не совпадало ни с чем.
    Ровно тот класс, от которого §18 и защищает: утверждение о своём формате
    пережило формат."""
    path, _, symbol = carrier.partition("::")
    return path, (symbol.strip() or None)


def _symbol_present(text: str, symbol: str) -> bool:
    """Символ объявлен в файле: функция, класс, присваивание или ключ.

    Намеренно грубо: адрес стережётся от ПРОТУХАНИЯ (переименовали, удалили),
    а не проверяется на смысл. Обещать больше — та же ошибка, что heartbeat
    вместо корректности."""
    pat = re.compile(
        rf"^\s*(?:async\s+def|def|class)\s+{re.escape(symbol)}\b"
        rf"|^\s*{re.escape(symbol)}\s*[:=]",
        re.MULTILINE)
    return bool(pat.search(text))


def unresolved_carriers(claims: list[Claim], root: Path | None = None) -> list[str]:
    """Носители, которых нет на диске. Протухший адрес = тихо-зелёный механизм."""
    base = root or ROOT
    out: list[str] = []
    for c in claims:
        for p in c.carriers:
            path, symbol = split_carrier(p)
            target = base / path
            if not target.exists():
                out.append(f"{c.section} (L{c.line}): носитель не найден — {p}")
                continue
            if symbol:
                try:
                    text = target.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    out.append(f"{c.section} (L{c.line}): носитель нечитаем — {p}: {e}")
                    continue
                if not _symbol_present(text, symbol):
                    out.append(f"{c.section} (L{c.line}): символ не найден в носителе — {p}")
    return out


def touched(changed: list[str], text: str | None = None,
           root: Path | None = None) -> tuple[list[Hit], list[str]]:
    """→ (затронутые утверждения, ошибки). Ошибки непустые — доверять выбору нельзя."""
    base = root or ROOT
    if text is None and not (base / "CLAUDE.md").exists():
        return [], []        # открытая выгрузка: свода правил в ней нет (закрытая часть, 28.09)
    src = text if text is not None else (base / "CLAUDE.md").read_text(encoding="utf-8")
    claims, errors = parse_claims(src)
    errors += unresolved_carriers(claims, base)
    touched = set(changed)
    # Сравниваем по ФАЙЛУ: метка может адресовать символ внутри него
    # («файл.py::функция»), но изменённые пути приходят от git без символов.
    hits = [Hit(c, p) for c in claims for p in c.carriers
            if split_carrier(p)[0] in touched]
    return hits, errors


def unmarked_live_added(diff: str) -> list[str]:
    """Строки «Enforcement: **LIVE**», ДОБАВЛЕННЫЕ этим диффом и не несущие метку.

    Только добавленные, а не все в файле, — осознанно. Печатать легаси на каждом
    коммите в свод значит выучить читателя пропускать датчик (§13, banner-blindness);
    тот же прецедент, что «легаси не красится» у дубль-гейта. Догонять покрытие
    старых строк — отдельная работа с отдельным решением, а не побочный шум.
    """
    out: list[str] = []
    for ln in diff.split("\n"):
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        body = ln[1:]
        if _LIVE.search(body) and "⟨carriers:" not in body:
            out.append(body.strip())
    return out


def live_coverage(text: str) -> tuple[int, int]:
    """(сколько LIVE-строк с меткой, сколько всего). Число, а не ощущение:
    деградация покрытия обязана быть видимой, раз она не запрещена."""
    live = [l for l in text.split("\n") if _LIVE.search(l)]
    return sum(1 for l in live if "⟨carriers:" in l), len(live)


def _staged(root: Path | None = None) -> list[str]:
    """Из ИНДЕКСА, не из рабочего дерева (§15): судится то, что уйдёт в коммит."""
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         cwd=root or ROOT, capture_output=True, text=True, check=True)
    return [l for l in out.stdout.splitlines() if l]


def main() -> int:
    changed = sys.argv[1:] or _staged()
    if not changed:
        return 0
    hits, errors = touched(changed)
    for e in errors:
        print(f"affected_claims: ⛔ {e}", file=sys.stderr)
    for h in sorted(set(hits)):
        print(f"{h.claim.section} (CLAUDE.md:{h.claim.line}) ← {h.carrier}")

    if "CLAUDE.md" in changed:
        diff = subprocess.run(["git", "diff", "--cached", "-U0", "--", "CLAUDE.md"],
                              cwd=ROOT, capture_output=True, text=True).stdout
        new_bare = unmarked_live_added(diff)
        if new_bare:
            have, total = live_coverage(SVOD.read_text(encoding="utf-8"))
            print(f"unmarked-live: новых строк LIVE без метки носителя — {len(new_bare)} "
                  f"(покрытие класса: {have} из {total})", file=sys.stderr)
            for b in new_bare:
                print(f"unmarked-live:   {b[:110]}", file=sys.stderr)
    return 8 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
