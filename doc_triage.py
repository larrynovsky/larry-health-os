#!/usr/bin/env python3.11
"""doc_triage.py — что изображено на присланной картинке: форма документа и модальность.

Один домен: СУЖДЕНИЕ о содержимом кадра. Ни сохранения, ни БД, ни телеграма —
это делает вызывающий. Возвращает форму из закрытого множества, модальность
и признак, было ли это ответом модели или падением на безопасную сторону.

ДВЕ ОСИ, И ОНИ НЕ РАВНОПРАВНЫ.
Маршрут выбирает ФОРМА, потому что от формы зависит, чем документ разбирать:
таблицу «тест–значение–единицы–референс» читает один экстрактор, прозу-заключение
будет читать другой. МОДАЛЬНОСТЬ (лучевое, клиническое, эндоскопия…) маршрут не
выбирает — это атрибут, он едет сайдкаром рядом с файлом. Первая редакция (07-28,
утро) резала по модальности и сразу получила неразрешимый случай: заключение
рентгенолога — одновременно лучевое исследование и текст врача, и модель законно
выбрала не то. Форма такой двусмысленности не даёт: таблица либо есть, либо нет.

ГРАНИЦА «ДОКУМЕНТ ПРОТИВ ТЕЛА» — предпосылка оси, а не её часть. Ось формы имеет
смысл только внутри ветки «документ». Тело пациента документом не является НИКОГДА,
даже если это медицинский материал без таблицы и без текста: фото ногтя, сыпи,
раны — это вход в симптом-диалог, а не в инбокс документов. Правило «медматериал
без таблицы → report» без этой оговорки увело бы фото кожи в reports/ и отключило
симптом-интейк (найдено владельцем при разборе, до внедрения).

Безопасная сторона — LAB при любом сомнении. Асимметрия двойная: пропущенный
анализ уходит в свободный текст без оракула (инцидент 2026-07-28), а лишний файл
в инбоксе стоит одного ревью; и только у ветки LAB есть И распознаватель, И
сигнал на промах («класс lab, распознано 0 строк»). Ошибка в reports/ ляжет молча.

`fallback=True` — не деталь реализации, а измеряемая величина: без неё «модель
сказала lab» неотличимо от «модель не ответила», и shadow-режим перестаёт быть
замером.
"""
from __future__ import annotations

import base64
import io
import logging

log = logging.getLogger("doc_triage")

# ── Ось 1: ФОРМА. Выбирает маршрут. ───────────────────────────────────────────
LAB = "lab"              # таблица результатов: тест · значение · единицы · референс
REPORT = "report"        # документ учреждения без таблицы: заключение, протокол, выписка,
                         # направление, а также снимок/растр без текста
PERSONAL = "personal"    # не документ учреждения: тело пациента, еда, упаковка, чек, экран

_LABELS = (LAB, REPORT, PERSONAL)
SAFE_SIDE = LAB

# ── Ось 2: МОДАЛЬНОСТЬ. Маршрут НЕ выбирает, едет атрибутом. ──────────────────
_MODALITIES = ("laboratory", "imaging", "clinical", "endoscopy", "pathology", "other")
UNKNOWN_MODALITY = "other"

# Системная механика, не клиника (§9 класс-2): выше этого ребра vision-API всё
# равно ужимает картинку сам, а base64 распухает и упирается в лимит запроса.
_MAX_EDGE = 1568
_MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MODEL_ROLE = "haiku"

VERSION = "triage-2"     # едет в сайдкар: замер привязан к версии суждения

_PROMPT = (
    "Ответь РОВНО двумя словами через пробел: форма и модальность.\n"
    "\n"
    "ФОРМА — одно из:\n"
    f"{LAB} — бланк лаборатории с таблицей «тест — результат — единицы — референс».\n"
    f"{REPORT} — медицинский документ учреждения БЕЗ такой таблицы: заключение, "
    "протокол исследования, выписка, направление, рецепт; сюда же снимок или "
    "изображение исследования без текста.\n"
    f"{PERSONAL} — НЕ документ учреждения: часть тела человека (кожа, ноготь, рана, "
    "сыпь), еда, упаковка препарата, чек, скриншот, пейзаж. "
    "Тело человека — ВСЕГДА personal, даже если на нём видна болезнь.\n"
    "\n"
    "МОДАЛЬНОСТЬ — одно из: laboratory, imaging, clinical, endoscopy, pathology, other. "
    "Для personal пиши other.\n"
    "\n"
    f"Если сомневаешься в форме — пиши {LAB}. Никаких пояснений, только два слова."
)


def labels() -> tuple[str, ...]:
    """Закрытое множество форм. Единственный дом списка — здесь."""
    return _LABELS


def modalities() -> tuple[str, ...]:
    """Закрытое множество модальностей."""
    return _MODALITIES


def _fit(image: bytes) -> bytes:
    """Ужать длинное ребро до _MAX_EDGE. Не изображение/сбой PIL → отдать как есть
    (решение о валидности не наше: этот модуль судит содержимое, не форму файла)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(image))
        if max(im.size) <= _MAX_EDGE:
            return image
        ratio = _MAX_EDGE / max(im.size)
        im = im.convert("RGB").resize((max(1, int(im.width * ratio)),
                                       max(1, int(im.height * ratio))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        log.warning("doc_triage._fit: не ужал (%s) — отдаю исходные байты", e)
        return image


def _ask(client, model: str, image: bytes, media_type: str) -> str:
    resp = client.messages.create(task="doc_triage._ask",
        model=model, max_tokens=12,
        system="Отвечай двумя словами из предложенных списков, без пояснений.",
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(image).decode()}},
            {"type": "text", "text": _PROMPT},
        ]}],
    )
    return (next((b.text for b in resp.content if b.type == "text"), "") or "").strip().lower()


def _parse(raw: str) -> tuple[str | None, str]:
    """(форма или None, модальность). Форма ищется по вхождению — модель иногда
    добавляет пояснение вопреки инструкции, и терять из-за этого ответ незачем."""
    form = next((lab for lab in _LABELS if lab in raw), None)
    mod = next((m for m in _MODALITIES if m in raw), UNKNOWN_MODALITY)
    return form, mod


def classify_image(image: bytes, media_type: str = "image/jpeg", client=None) -> dict:
    """Форма и модальность кадра.

    Возвращает {label, modality, raw, fallback, reason, version}:
      label    — одна из labels(), выбирает маршрут;
      modality — одна из modalities(), маршрут НЕ выбирает;
      raw      — что ответила модель ("" если не спрашивали или не ответила);
      fallback — True, если форму выбрали не по ответу модели, а по безопасной стороне;
      reason   — почему сработала безопасная сторона ("" если не срабатывала).
    Не бросает: любой сбой — это fallback на SAFE_SIDE, потому что молчаливый
    отказ приёма анализов дороже лишнего файла в инбоксе.
    """
    out = {"label": SAFE_SIDE, "modality": UNKNOWN_MODALITY, "raw": "",
           "fallback": True, "reason": "", "version": VERSION}
    if not image:
        return {**out, "reason": "пустой вход"}
    if media_type not in _MEDIA_TYPES:
        return {**out, "reason": f"media_type {media_type!r} вне {_MEDIA_TYPES}"}
    try:
        import hai_core
        if client is None:
            client = hai_core.get_client()
        raw = _ask(client, hai_core.get_model(_MODEL_ROLE), _fit(image), media_type)
    except Exception as e:
        log.warning("doc_triage.classify_image: сбой vision (%s) — безопасная сторона %s",
                    e, SAFE_SIDE)
        return {**out, "reason": f"сбой vision: {e}"}
    form, mod = _parse(raw)
    if form is None:
        log.warning("doc_triage.classify_image: ответ %r без формы — безопасная сторона %s",
                    raw, SAFE_SIDE)
        return {**out, "raw": raw, "modality": mod, "reason": "форма вне множества"}
    return {**out, "label": form, "modality": mod, "raw": raw, "fallback": False}
