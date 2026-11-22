#!/usr/bin/env python3.11
"""stderr_watch.py — ошибки, которые пишутся в stderr джоб и которых не видит НИКТО.

Зачем (замер 2026-09-13). Весь поток, доходивший до владельца, жил в предупреждениях
integrity. А настоящие ошибки — трассировки, «команда не найдена», «файл только для
чтения» — оседают в stderr-логах launchd-джоб, у которых нет ни одного читателя. В тот
день вотчер авто-тестов девять суток писал туда `mkdir: //logs: Read-only file system`,
следил за всей файловой системой вместо репозитория, и ни один датчик этого не заметил:
PID у него был (KeepAlive исправно перезапускал), а liveness ≠ корректность (§14).
Первый же замер по 38 джобам нашёл ошибки в семи логах.

ПОЧЕМУ СМЕЩЕНИЕ, А НЕ ХВОСТ И НЕ ВРЕМЯ. Хвост в N строк ловит древние трассировки в
медленно растущих логах (проверено: у планировщика опросников трассировки трёхмесячной
давности попали бы в находку сегодня). По времени фильтровать нельзя — строка
`mkdir: ... Read-only file system` не несёт отметки времени вовсе. Остаётся единственный
честный признак: читать ТОЛЬКО то, что дописано с прошлой проверки. Он же сам себя
калибрует — первый прогон лишь запоминает позиции и не находит ничего.

Граница названа вслух: датчик судит ТЕКСТ, а не поведение. Джоба, молча делающая не то
(ничего не пишет в stderr), для него невидима — её ловят другие датчики. И наоборот:
строка, похожая на ошибку, может быть безобидной — поэтому класс находки `fix`
(инженерная очередь), а не `decide`: будить владельца текстом из лога нельзя.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import time
from pathlib import Path

LABEL_PREFIX = "com.larry.health"

# Сигнатуры, а не «любая строка»: stderr законно используется под INFO-логи (так пишут
# calendar_sync и lab_intake), и «есть строки» находкой не является.
ERROR_SIGNS = re.compile(
    r"Traceback \(most recent call last\)"
    r"|No such file or directory"
    r"|Read-only file system"
    r"|command not found"
    r"|Permission denied"
    r"|ModuleNotFoundError"
    r"|SyntaxError"
    r"|CRITICAL",
)


def _state_path() -> Path:
    p = os.environ.get("HEALTH_STDERR_WATCH_STATE")
    return Path(p) if p else Path(__file__).parent / "logs" / "stderr_watch_state.json"


def _agents_dir() -> Path:
    # Один дом ответа «где плисты этой установки» (docker-install, этап 2a, 2026-09-29):
    # прежний свой шов HEALTH_LAUNCH_AGENTS погашен — второе имя одного факта.
    import plist_env_liveness
    return plist_env_liveness.agents_dir()


def _err_logs() -> list[tuple[str, Path]]:
    """(label, путь к stderr-логу) по нашим плистам. Плист без StandardErrorPath —
    законно (логов не ведёт), пропускаем молча."""
    out = []
    for p in sorted(_agents_dir().glob(f"{LABEL_PREFIX}*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception:  # silent-ok: битый чужой плист — не наша находка, соседи важнее
            continue
        err = d.get("StandardErrorPath")
        label = d.get("Label") or p.stem
        if err:
            out.append((str(label), Path(str(err))))
    return out


def new_errors(save: bool = True) -> list[dict]:
    """Ошибки, ДОПИСАННЫЕ в stderr-логи с прошлой проверки.

    Возврат: [{label, path, hits, sample}]. Первый прогон возвращает пусто и лишь
    запоминает позиции — датчик не должен родиться красным на накопленной истории.

    Усечение/ротация (размер стал меньше запомненной позиции) обрабатывается как
    «начали заново»: позиция сбрасывается, находка в этот прогон НЕ выдаётся, иначе
    каждая ротация давала бы ложный залп."""
    state_path = _state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    first_run = not state
    found = []
    new_state = {}
    for label, path in _err_logs():
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            # Лога ещё нет (джоба ни разу не писала в stderr) — это не слепота, а
            # позиция 0: запоминаем, иначе blind_spots считал бы его неотслеживаемым
            # (карточка 28.09: «объявляют 28, отслеживает 12»), а первая ошибка в
            # появившемся файле пропускалась бы как «новый лейбл».
            # Только если каталог лога есть: нет каталога — писать джобе некуда, это слепота.
            if path.parent.is_dir():
                new_state[label] = {"offset": 0, "path": str(path)}
            continue
        except OSError:
            # Отказ доступа и прочее — наблюдать нельзя: лейбл выпадает из состояния и
            # остаётся видимым для blind_spots; после восстановления — калибровка заново
            # (старые строки не выдаются повторно). Ревью ночной починки 01.10, пп.1–2.
            continue
        known = label in state
        prev = int(state.get(label, {}).get("offset", 0) or 0)
        new_state[label] = {"offset": size, "path": str(path)}
        if first_run or not known or size < prev or size == prev:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(prev)
                chunk = f.read(size - prev)
        except OSError:
            continue
        lines = chunk.splitlines()
        hits = [i for i, ln in enumerate(lines) if ERROR_SIGNS.search(ln)]
        if hits:
            found.append({"label": label, "path": str(path),
                          "hits": len(hits), "sample": _sample(lines, hits[0])[:160]})
    if save:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except OSError as e:
            # Не тихо: без сохранения позиций датчик слепнет на следующий прогон.
            print(f"stderr_watch: позиции не сохранены: {e!r}")
    return found


_TB_HEAD = re.compile(r"Traceback \(most recent call last\)")
_TB_CHAIN = ("During handling of the above exception", "The above exception was")
# Итог исключения: имя (с модулем) на Error/Exception/Group/Exit/Interrupt/Timeout, затем «:» или
# конец строки. Строка вне этой формы — уже не трассировка (ревью 18.11: иначе образцом становилась
# чужая строка лога после незакрытой трассировки).
_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Group|Exit|Interrupt|Timeout)(:|$)")


def _sample(lines: list[str], i: int) -> str:
    """Строка-образец находки. Для трассировки — её ИТОГ («ValueError: ...»), а не заголовок:
    карточка 18.11 по боту несла «Traceback (most recent call last):» — причину по ней не назвать.

    Поиск ограничен блоком трассировки: строки с отступом — кадры; цепочка «During handling…»
    продолжает поиск до последнего исключения; первая строка без отступа, не похожая на итог, —
    конец блока. Итога нет (обрыв концом куска, обрыв внутри цепочки, чужая строка) — честный
    откат на заголовок. Группа исключений (3.11+, «+ Exception Group Traceback», все строки
    с отступом «| …») — итог берётся из её строки «| ExceptionGroup: …»."""
    head = lines[i].strip()
    if not _TB_HEAD.search(head):
        return head
    group = head.lstrip("+ ").startswith("Exception Group")
    last = None
    for ln in lines[i + 1:]:
        if not ln.strip():
            continue
        if group:
            s = ln.strip().lstrip("|").strip()
            if _EXC_LINE.match(s):
                return s
            if not ln[:1].isspace():
                break
            continue
        if ln[:1].isspace() or _TB_HEAD.search(ln):
            continue
        if ln.startswith(_TB_CHAIN):
            last = None          # дальше — следующее исключение цепочки; без него итога нет
            continue
        if _EXC_LINE.match(ln.strip()):
            last = ln.strip()    # итог; цепочка может продолжиться
            continue
        break                    # чужая строка — блок трассировки кончился
    return last or head


def exception_class(stderr_text: str, returncode: int) -> str:
    """Класс сбоя подпроцесса по его stderr — токен для detail журнала сбоев, без текста ошибки
    (в тексте бывают URL и ответы провайдера). Исходы различимы, а не сведены в одно «нет»
    (ревью починки 2026-11-17-46973d29):
      signal_N     — процесс убит сигналом (код < 0), трассировки нет по построению;
      no_traceback — заголовка трассировки нет;
      unparsed     — трассировка есть, итог не разобран (обрыв); заметка add_note после итога не мешает;
      <Class>      — последний сегмент имени исключения (URLError, ExceptionGroup, …)."""
    if returncode < 0:
        return f"signal_{-returncode}"
    lines = (stderr_text or "").splitlines()
    heads = [i for i, ln in enumerate(lines) if _TB_HEAD.search(ln)]
    if not heads:
        return "no_traceback"
    s = _sample(lines, heads[0])
    if _TB_HEAD.search(s) or not _EXC_LINE.match(s):
        return "unparsed"
    return s.split(":", 1)[0].rsplit(".", 1)[-1]


def blind_spots(max_age_hours: float = 26.0) -> list[str]:
    """Чем датчик СЛЕП прямо сейчас. Пустой список = видит. Причины — словами.

    Мина, ради которой это написано (названа в снимке нити 13.09): датчик, у
    которого стёрлось или не создалось состояние, возвращает ПУСТО — и «ошибок
    нет» неотличимо от «я ничего не смотрел». Это тот же класс, что породил сам
    stderr_watch: PID есть, а работы нет (§14, liveness ≠ корректность).

    Смотрит на две разные слепоты:
      • состояние не создано или не обновлялось дольше max_age_hours — датчик
        не отрабатывал;
      • плисты объявляют N stderr-логов, а в состоянии отслеживается меньше —
        датчик смотрит не туда (ровно случай «следил за / вместо репозитория»).

    ПОРЯДОК ВЫЗОВА ЗНАЧИМ. Возраст состояния честен, только пока new_errors его
    не переписал: в одном процессе сначала эта проверка, потом сбор ошибок.
    Сторож порядка — tests/unit/test_stderr_watch.py::test_liveness_check_runs_
    before_error_check; без него зелёный был бы причинён порядком строк, а не
    работой датчика (§20).
    """
    out = []
    state_path = _state_path()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        age_h = (time.time() - state_path.stat().st_mtime) / 3600.0
    except (OSError, ValueError):
        return ["состояние stderr-датчика не создано или нечитаемо — "
                "он ни разу не отработал, и его «ошибок нет» ничего не значит"]
    if age_h > max_age_hours:
        out.append(f"состояние stderr-датчика не обновлялось {age_h:.0f} ч "
                   f"(порог {max_age_hours:.0f}) — датчик не отрабатывает")
    logs = _err_logs()
    declared = len(logs)
    tracked = len(raw)
    if declared and tracked < declared:
        # Имена и причины, а не только счёт: «18 из 28» без имён чинить нечем —
        # причина обычно в среде, и инженеру надо знать, ГДЕ (карточка 28.09, вторая ночь).
        missing = [f"{lbl}: {_why_untracked(p)}" for lbl, p in logs if lbl not in raw]
        out.append(f"плисты объявляют {declared} stderr-логов, датчик отслеживает "
                   f"{tracked} — он смотрит не на всё, что пишет ошибки"
                   + (f"; не отслеживаются: {', '.join(missing)}" if missing else ""))
    return out


def _why_untracked(path: Path) -> str:
    """Причина словами — те же ветки, по которым new_errors выбрасывает лейбл."""
    if not path.parent.is_dir():
        return f"нет каталога {path.parent}"
    try:
        path.stat()
    except FileNotFoundError:
        return "файла нет — встанет на учёт следующим прогоном"
    except PermissionError:
        return f"нет доступа к {path}"
    except OSError as e:
        return f"не читается ({e.__class__.__name__})"
    return "не было на прошлом прогоне — встанет на учёт следующим"


if __name__ == "__main__":
    for b in blind_spots():
        print(f"СЛЕПОТА: {b}")
    for f in new_errors(save=False):
        print(f"{f['label']:45} +{f['hits']:4d} | {f['sample']}")
