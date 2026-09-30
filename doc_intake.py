#!/usr/bin/env python3.11
"""doc_intake.py — приём медицинского документа, присланного КАРТИНКОЙ.

Один домен: «куда этот кадр положить». Классификацию спрашивает у doc_triage,
безопасное сохранение — у media_intake, сам ничего не решает про телеграм и
ничего не отвечает пользователю: текст ответа собирает обработчик.

Маршруты. В корень инбокса тенанта попадает ТОЛЬКО lab — потому что только его
там ждёт lab_intake_watcher. report ложится в reports/ и ждёт своего разбирателя
прозы. personal на диск не пишется вовсе: фото тела и еды в инбоксе меддокументов —
это и шум в ревью, и данные не там, где их ждут.

Рядом с принятым файлом кладётся сайдкар <имя>.triage.json: форма, модальность,
сырой ответ модели, признак безопасной стороны, версия классификатора. Без него
связка «кадр → метка» существовала бы только как сопоставление по времени в логе,
то есть сверять решения классификатора было бы нечем. Формат — продолжение уже
живущей в этом инбоксе конвенции .failed/.norows.

Известный второй дом: путь инбокса выводит ещё и lab_intake_watcher._incoming,
но из DB_PATH, а не из HEALTH_DATA_DIR. Формулы разные, результат совпадает
(<tenant>/data/health.db → <tenant>). Сводить в этой ветке не стал: watcher —
потребитель, его касание расширило бы периметр правки на боевой демон.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import doc_triage

log = logging.getLogger("doc_intake")

# Подкаталог инбокса по ФОРМЕ. None = на диск не кладём.
# Модальность каталог НЕ выбирает: она атрибут и едет сайдкаром (решение владельца
# 2026-07-28). Раскладка каталогами по модальности была отвергнута — смысл переехал
# бы в имя каталога, документ на стыке двух модальностей девать было бы некуда,
# и обратно в таблицу такая раскладка не собирается.
_SUBDIR = {
    doc_triage.LAB: "",
    doc_triage.REPORT: "reports",
    doc_triage.PERSONAL: None,
}

SIDECAR_SUFFIX = ".triage.json"


def tenant_inbox(base: str | None = None) -> Path:
    """Инбокс документов тенанта: HEALTH_DATA_DIR/incoming."""
    root = base or os.environ.get("HEALTH_DATA_DIR") or str(Path.home() / "health")
    return Path(root) / "incoming"


def destination(label: str, base: str | None = None) -> Path | None:
    """Каталог для класса или None, если класс на диск не кладётся."""
    sub = _SUBDIR.get(label, "")
    if sub is None:
        return None
    inbox = tenant_inbox(base)
    return inbox / sub if sub else inbox


def intake(data: bytes, media_type: str = "image/jpeg", base: str | None = None,
           store: bool = True, client=None) -> dict:
    """Классифицировать кадр и (если store) положить в каталог его класса.

    Возвращает {label, fallback, raw, stored, path, is_duplicate, error}:
      stored — легло ли на диск (False для personal, для store=False и при отказе);
      error  — почему не легло, если причина не «класс personal» и не store=False.

    store=False — режим наблюдения: классификация происходит и попадает в лог,
    маршрут не меняется. Иначе замерить классификатор было бы нечем, кроме как
    включив его в бой.

    Не бросает: приём документа не должен падать из-за отказа хранилища —
    пользователь в этот момент стоит у кабинета врача, а не у консоли.
    """
    verdict = doc_triage.classify_image(data, media_type, client=client)
    out = {"label": verdict["label"], "modality": verdict["modality"],
           "fallback": verdict["fallback"], "raw": verdict["raw"],
           "stored": False, "path": None, "is_duplicate": False, "error": ""}
    dest = destination(out["label"], base)
    if dest is None:
        return out
    if not store:
        out["error"] = "режим наблюдения: маршрут не менялся"
        return out
    try:
        import media_intake
        res = media_intake.sanitize_and_store(data, dest_dir=dest)
        out.update(stored=True, path=res["path"], is_duplicate=res["is_duplicate"])
    except Exception as e:
        log.error("doc_intake: %s не сохранён (%s)", out["label"], e, exc_info=True)
        out["error"] = str(e)
        return out
    # Сайдкар пишется ВНЕ try приёма: его отказ не имеет права пометить приём
    # неудавшимся. Файл уже на диске и будет разобран; потеря атрибута хуже, но не
    # смертельна — в отличие от потери самого документа. Свой try здесь не
    # избыточен рядом с внутренним: контракт intake обещает «не бросает», и это
    # обещание не должно зависеть от дисциплины помощника.
    try:
        _write_sidecar(Path(out["path"]), out, verdict)
    except Exception as e:  # noqa: BLE001
        log.warning("doc_intake: сайдкар не записан (%s) — приём НЕ провален", e)
    return out


def _write_sidecar(path: Path, out: dict, verdict: dict) -> None:
    """Атрибуты приёма рядом с файлом. Сбой сайдкара НЕ отменяет приём: файл уже
    лёг и будет разобран, потеря атрибута хуже, но не смертельна — в отличие от
    потери самого документа."""
    import json
    try:
        path.with_name(path.name + SIDECAR_SUFFIX).write_text(json.dumps({
            "form": out["label"],
            "modality": out["modality"],
            "raw": verdict.get("raw", ""),
            "fallback": verdict.get("fallback"),
            "reason": verdict.get("reason", ""),
            "classifier_version": verdict.get("version", ""),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("doc_intake: сайдкар не записан для %s (%s)", path.name, e)
