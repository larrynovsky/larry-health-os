"""
tests/unit/test_import_medical_events_cli.py — характеризация F-02/F-03.

Нить: ремонт `data_ingestion`, аудит `data_ingestion@2026-07-27-b2306c4`, Фаза A (A5).
Красный на baseline `e4617ff` — ЭТО ОЖИДАЕМО. Зелёный он обязан стать только после
Фазы C, и ни секундой раньше.

ЧТО ОХРАНЯЕТ (три отдельных свойства, три отдельных теста):
  A5.1  bulk-режим перечисляет непустой каталог и доходит до обработчика документа.
        Сейчас фильтр расширений делает `(".pdf",) | IMAGE_EXTS` — `tuple | set` даёт
        TypeError ПРИ ВЫЧИСЛЕНИИ генератора, до первого `process_pdf`. Живой лог
        watcher'а содержит 230 точных повторов этой ошибки.
  A5.2  `main()` возвращает КОД, а не None. Сейчас в bulk-ветке нет ни `return`, ни
        `sys.exit`, поэтому `__main__` завершает процесс нулём при любом исходе.
  A5.3  Отказ обработчика отображается в non-zero и НЕ пробрасывается наружу.
        Сейчас различить нормальный skip и отказ OCR/LLM/DB нечем: оба дают `False`.

ЧЕГО ЭТОТ ФАЙЛ НЕ ДОКАЗЫВАЕТ (граница, чтобы зелёный не переоценили):
  - что shell-обёртка `watch_and_import.sh` увидит код возврата — это A7, отдельный
    subprocess-тест; здесь `main()` зовётся внутри процесса;
  - что документ реально распознан — `process_pdf` замокан целиком.

ОРАКУЛЫ объявлены ДО написания (RST, ловушка «наличие оракула ≠ адекватность оракула»):
A5.1 — `process_pdf.call_count >= 1`; A5.2 — `rc == 0` при отсутствии отказов;
A5.3 — `rc != 0` и отсутствие проброшенного исключения.

Изоляция: только `tmp_path`, `--dry-run` и mock. Ни сети, ни production DB, ни уведомлений.
"""
from __future__ import annotations

import sys
from unittest import mock

import pytest

import import_medical_events as mod


@pytest.fixture
def cr_dir(tmp_path):
    """Непустой каталог документов: один PDF и одно изображение.

    Содержимое намеренно пустое — распознавание замокано, проверяется ПЕРЕЧИСЛЕНИЕ.
    Два разных расширения нужны потому, что дефект живёт именно в объединении
    коллекций разных типов, а не в обработке `.pdf` как таковой.
    """
    (tmp_path / "probe.pdf").touch()
    (tmp_path / "probe.jpeg").touch()
    return tmp_path


def _run(cr_dir, monkeypatch, process_pdf):
    """Один прогон bulk-режима. Возвращает (rc, mock) либо пробрасывает исключение.

    `rc` намеренно не нормализуется: None — тоже ответ, и именно его ловит A5.2.
    """
    monkeypatch.setattr(sys, "argv",
                        ["import_medical_events.py", "--dir", str(cr_dir), "--dry-run"])
    with mock.patch.object(mod, "process_pdf", process_pdf) as m:
        return mod.main(), m


# Пометка xfail(strict) снята 2026-07-29: F-02 закрыт — расширения вынесены в
# константу DOC_EXTS, выражения `tuple | set` больше нет. XPASS(strict) сработал
# ровно так, как обещала записка в пометке, и заставил её снять.
def test_a5_1_bulk_reaches_handler_on_nonempty_dir(cr_dir, monkeypatch):
    """A5.1 — непустой каталог доходит до обработчика.

    Baseline: TypeError на `tuple | set` до первого вызова. Зелёный: обработчик
    вызван хотя бы раз (сколько именно — не фиксируем: число файлов не контракт).
    """
    handler = mock.Mock(return_value=True)
    _rc, m = _run(cr_dir, monkeypatch, handler)
    assert m.call_count >= 1, (
        "bulk-режим не дошёл до process_pdf на непустом каталоге — "
        "перечисление файлов сломано до обработки"
    )


@pytest.mark.xfail(strict=True, reason=(
    "F-03: `main()` не возвращает код — успешный прогон отдаёт None. "
    "Зелёным станет в Фазе C."))
def test_a5_2_main_returns_zero_code_on_success(cr_dir, monkeypatch):
    """A5.2 — успешный прогон отдаёт КОД 0, а не None.

    `None` здесь не «почти ноль»: `__main__` вызывает `main()` без `sys.exit`, поэтому
    отсутствие возврата делает ЛЮБОЙ исход успехом для shell. Это и есть F-03.
    """
    _rc, _m = _run(cr_dir, monkeypatch, mock.Mock(return_value=True))
    assert _rc == 0, (
        f"main() вернул {_rc!r} вместо кода 0 — shell не может отличить успех от отказа"
    )


@pytest.mark.xfail(strict=True, reason=(
    "F-03: отказ обработчика не доезжает до кода возврата процесса. "
    "Зелёным станет в Фазе C."))
def test_a5_3_handler_failure_maps_to_nonzero(cr_dir, monkeypatch):
    """A5.3 — отказ обработчика становится non-zero и не пробрасывается наружу.

    Отказ смоделирован исключением, а не `False`: `False` сегодня означает И нормальный
    idempotent-skip, И отказ OCR/LLM/DB, поэтому через него контракт выразить нельзя.
    Требование: job с отказом обязан отличаться от job без отказа кодом возврата.

    Проброс наружу — тоже провал: watcher зовёт CLI как процесс, а не как функцию;
    неперехваченное исключение даёт трейсбек вместо структурированного результата.
    """
    boom = mock.Mock(side_effect=RuntimeError("planted: handler failure"))
    try:
        rc, m = _run(cr_dir, monkeypatch, boom)
    except RuntimeError as exc:
        pytest.fail(
            f"отказ обработчика пробросился наружу вместо кода возврата: {exc}"
        )
    assert m.call_count >= 1, "обработчик не был вызван — тест не проверил то, что должен"
    assert rc not in (0, None), (
        f"job с отказом вернул {rc!r} — неотличимо от полного успеха"
    )
