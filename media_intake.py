"""media_intake.py — безопасный приём изображений для visual-intake (WP1).

WSTG-BUSL-09: не доверяем расширению/имени файла отправителя.
- Валидация: реально ли это изображение (PIL-декод); ImageRejected иначе.
- Pixel-bomb guard: лимит суммарных пикселей (decompression bomb).
- EXIF-strip: пересохранить БЕЗ метаданных (фото тела не должно нести GPS).
- Имя из sha256 ИСХОДНЫХ байт (не из имени TG — path traversal); дедуп по нему.

Хранение — локально (HEALTH_DATA_DIR/media/visual), только primary (вызов с
primary-хоста; сам файл пишем на диск, БД-строку добавляет visual_db в WP4).
MAX_* — системная механика (§9 класс-2: предохранительные лимиты, не клиника).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

MAX_PIXELS = 40_000_000            # ~40 Мп — выше любого телефона, ловит pixel-bomb
MAX_BYTES = 25 * 1024 * 1024       # 25 МБ вход
_EXT_BY_FMT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class ImageRejected(Exception):
    """Вход не является безопасным изображением (не декодируется / бомба / пусто)."""


def visual_media_dir(base: str | None = None) -> Path:
    base = base or os.environ.get("HEALTH_DATA_DIR") or str(Path.home() / "health")
    d = Path(base) / "media" / "visual"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _strip_and_reencode(data: bytes) -> tuple[bytes, str]:
    """Декод → лимиты → пересохранение без метаданных. (clean_bytes, ext). Бросает ImageRejected."""
    from PIL import Image
    try:
        Image.open(io.BytesIO(data)).verify()          # структурная проверка
        im = Image.open(io.BytesIO(data))               # verify() «расходует» объект — переоткрыть
        im.load()
    except ImageRejected:
        raise
    except Exception as e:
        raise ImageRejected(f"не изображение: {e}")
    w, h = im.size
    if w * h > MAX_PIXELS:
        raise ImageRejected(f"слишком большое ({w}x{h}={w*h}px > {MAX_PIXELS}, pixel-bomb guard)")
    fmt = (im.format or "JPEG").upper()
    ext = _EXT_BY_FMT.get(fmt, ".jpg")
    save_fmt = "JPEG" if ext == ".jpg" else fmt
    # Ориентацию ПРИМЕНИТЬ до обнуления метаданных. Телефон пишет растр в сенсорной
    # ориентации + тег Orientation; выкинув тег без поворота, мы отдаём распознавателю
    # картинку боком. Отказ при этом молчаливый — часть строк просто не прочитается и
    # будет неотличима от «их не было в документе» (риск R4 плана 2026-07-28).
    from PIL import ImageOps
    im = ImageOps.exif_transpose(im) or im
    # Чистый растр без EXIF/info: paste в новый объект (не копирует метаданные).
    if save_fmt == "JPEG" and im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    clean = Image.new(im.mode, im.size)
    clean.paste(im)
    buf = io.BytesIO()
    clean.save(buf, format=save_fmt)
    return buf.getvalue(), ext


def sanitize_and_store(data: bytes, base: str | None = None,
                       dest_dir: str | Path | None = None) -> dict:
    """Валидирует, чистит EXIF, сохраняет под sha256-именем. Дедуп по исходным байтам.
    Возвращает {path, sha256, exif_stripped, is_duplicate}. Бросает ImageRejected.

    dest_dir — каталог назначения вместо media/visual (приём меддокументов кладёт
    в инбокс тенанта). Задаёт ВЫЗЫВАЮЩИЙ КОД, не пользователь: имя файла всё равно
    строится из sha256 содержимого, поэтому траверсал через имя невозможен, но
    подставлять сюда что-то пришедшее из сети нельзя (WSTG-BUSL-09).
    """
    if not data:
        raise ImageRejected("пустой вход")
    if len(data) > MAX_BYTES:
        raise ImageRejected(f"слишком большой файл ({len(data)} > {MAX_BYTES})")
    orig_sha = hashlib.sha256(data).hexdigest()
    clean, ext = _strip_and_reencode(data)
    if dest_dir is not None:
        d = Path(dest_dir)
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = visual_media_dir(base)
    dest = d / f"{orig_sha}{ext}"
    if dest.exists():
        return {"path": str(dest), "sha256": orig_sha, "exif_stripped": True, "is_duplicate": True}
    dest.write_bytes(clean)
    return {"path": str(dest), "sha256": orig_sha, "exif_stripped": True, "is_duplicate": False}
