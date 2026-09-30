#!/usr/bin/env python3.11
"""Вердикт по карантинной паре — единственный способ снять `pending_adjudication`.

Зачем CLI, а не кнопка в Telegram (решение владельца 2026-07-26): вердикт выносится глядя на
числа, а не с телефона одним пальцем. Пара, впервые вошедшая в pass-set, — это ещё не
находка; решение «пускать в веру или нет» принимает человек, пока нет онлайн-контроллера §5.

Без обоснования вердикт не принимается: причина — то единственное, что прочитает будущий
читатель, когда спросит «почему эта связь в конституции».

    python3.11 scripts/adjudicate_quarantine.py                       # что висит
    python3.11 scripts/adjudicate_quarantine.py --all                 # вся история
    python3.11 scripts/adjudicate_quarantine.py --pair "sleep_total×hrv" \
        --verdict admit --reason "держится 4 прогона, эпохи не менялись"
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quarantine_db  # noqa: E402

_VERDICTS = {"admit": "admitted", "reject": "rejected"}

# Коды выхода — контракт для человека И для будущего онлайн-контроллера §5. Сырой traceback
# (VG-R4-08) не различал «БД занята» и «пары нет»: оператор видел `OperationalError: database
# is locked` и не знал, повторять или искать пару. Разные коды — разные действия.
EXIT_OK, EXIT_NOT_FOUND, EXIT_USAGE, EXIT_DB = 0, 1, 2, 3

_RETRIES, _BACKOFF = 5, 0.4   # ночной longitudinal держит запись секунды, не минуты


def _with_retry(fn, what: str):
    """Повтор при временной занятости БД. `database is locked` от параллельного прогона —
    состояние, которое проходит само; выдавать его оператору как крэш значит учить человека
    игнорировать ошибки. Постоянные ошибки (нет таблицы, битый файл) НЕ повторяем — они
    от повторов не лечатся, и маскировать их задержкой хуже, чем сказать сразу."""
    for attempt in range(_RETRIES):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            _msg = str(exc).lower()
            if "locked" not in _msg and "busy" not in _msg:
                raise
            if attempt == _RETRIES - 1:
                raise
            _wait = _BACKOFF * (2 ** attempt)
            print(f"  ⏳ БД занята ({exc}); повтор {attempt + 2}/{_RETRIES} через {_wait:.1f}с "
                  f"[{what}]", file=sys.stderr)
            time.sleep(_wait)


def _print(rows, title):
    print(f"\n{title}: {len(rows)}")
    for r in rows:
        # Пустые/NULL поля не роняют вывод: строка вне контракта — это находка датчика
        # corrupt_quarantine_rows, а не повод оставить оператора без списка.
        _entered = str(r.get("entered_at") or "")[:10] or "?"
        _age = r.get("age_days")
        line = (f"  [{str(r.get('status')):8}] {str(r.get('pair')):32} "
                f"{str(r.get('family')):6} вошла {_entered} "
                f"({_age if _age is not None else '?'}д) эпоха={r.get('method_epoch')!r}")
        if r.get("status") != "pending":
            line += f" → {r.get('resolution')} ({r.get('resolved_by')}, " \
                    f"{str(r.get('resolved_at') or '')[:10]})"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Вердикт по карантину мерцающих пар")
    ap.add_argument("--pair", help="пара в формате «a×b» или «pred→tgt+Nд»")
    ap.add_argument("--verdict", choices=sorted(_VERDICTS), help="admit = пустить в веру")
    ap.add_argument("--reason", help="обоснование (обязательно; его прочитает будущий читатель)")
    ap.add_argument("--all", action="store_true", help="показать всю историю, не только pending")
    ap.add_argument("--epoch", help="эпоха метода (по умолчанию текущая); менять только зная зачем")
    a = ap.parse_args()

    try:
        # ЭПОХА БЕРЁТСЯ ЯВНО. Раньше здесь был дефолт `''` из сигнатуры resolve_quarantine, а
        # гейт ставит пары под `signal_family_vN` — совпадения не было НИКОГДА, и CLI не мог
        # снять ни одной пары (найдено и воспроизведено 2026-07-26). Список при этом пару
        # показывал: инструмент выглядел рабочим, будучи мёртвым.
        epoch = a.epoch if a.epoch is not None else quarantine_db.method_epoch()

        if not a.pair:
            _print(_with_retry(
                lambda: quarantine_db.quarantine_rows(status=None if a.all else "pending"),
                "чтение списка"),
                "История карантина" if a.all else "Ждут вердикта")
            _corrupt = _with_retry(quarantine_db.corrupt_quarantine_rows, "проверка контракта")
            if _corrupt:
                print(f"\n⚠️ строк вне контракта: {len(_corrupt)} — они НЕ видны читателю веры:")
                for r in _corrupt[:10]:
                    print(f"  id={r['id']} {r['pair']}: {r['why']}")
            print(f"\nТекущая эпоха метода: {epoch}")
            return EXIT_OK

        if not a.verdict or not a.reason:
            print("⛔ нужны --verdict и --reason: вердикт без обоснования не принимается")
            return EXIT_USAGE

        n = _with_retry(lambda: quarantine_db.resolve_quarantine(
            a.pair, _VERDICTS[a.verdict], a.reason, method_epoch=epoch), "вердикт")
        if not n:
            print(f"⛔ пара «{a.pair}» не найдена среди pending эпохи {epoch}. "
                  f"Проверь список без аргументов (он печатает эпоху каждой строки).")
            return EXIT_NOT_FOUND
        print(f"✅ {a.pair}: {_VERDICTS[a.verdict]} — {a.reason}")
        _print(_with_retry(lambda: quarantine_db.quarantine_rows("pending"), "чтение остатка"),
               "Осталось ждать вердикта")
        return EXIT_OK

    except sqlite3.OperationalError as exc:
        # Голый traceback здесь был бесполезен оператору (VG-R4-08): он сообщает Python-тип,
        # а не что делать. Ниже — состояние и действие.
        print(f"⛔ база недоступна: {exc}", file=sys.stderr)
        print("   Обычные причины: идёт ночной longitudinal (подожди и повтори) либо "
              "init_db не прогонялся после 2026-07-26 (таблицы passset_quarantine нет).",
              file=sys.stderr)
        return EXIT_DB
    except sqlite3.DatabaseError as exc:
        print(f"⛔ база повреждена или это не SQLite: {exc}", file=sys.stderr)
        print("   Повтор не поможет. Проверь ~/health/data/health.db.", file=sys.stderr)
        return EXIT_DB
    except ValueError as exc:                      # контрактные отказы resolve_quarantine
        print(f"⛔ {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
