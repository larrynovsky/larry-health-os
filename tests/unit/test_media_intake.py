"""Характеризационные контроли media_intake (модуль был без тестов вовсе, 2026-07-28).

Главный новый контроль — ориентация: телефон пишет растр боком плюс тег EXIF
Orientation. Обнулив тег без поворота, мы отдавали бы распознавателю картинку
набок, и отказ был бы МОЛЧАЛИВЫМ: часть строк не прочиталась бы, и это
неотличимо от «их не было в документе». Контроль краснеет на снятом
exif_transpose — проверено (без него размер остаётся 20x10).

Остальное — фиксация уже существовавшего поведения WSTG-BUSL-09 (валидация
декодированием, pixel-bomb, имя из sha256, дедуп), чтобы правка каталога
назначения не сдвинула его незаметно.
"""
import io

import pytest
from PIL import Image

import media_intake


def _jpeg(w=20, h=10, orientation=None, color=(200, 30, 30)) -> bytes:
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation          # 0x0112 = Orientation
        im.save(buf, format="JPEG", exif=exif)
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()


def _png(w=8, h=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


# --- ориентация (новое, R4) ----------------------------------------------------

def test_orientation_6_applied_before_exif_stripped(tmp_path):
    """Orientation=6 («повернуть на 90») обязан примениться к растру: 20x10 → 10x20."""
    r = media_intake.sanitize_and_store(_jpeg(20, 10, orientation=6), dest_dir=tmp_path)
    assert Image.open(r["path"]).size == (10, 20)


def test_orientation_absent_leaves_geometry_alone(tmp_path):
    """Без тега ничего не крутим — иначе починка ориентации сама стала бы поломкой."""
    r = media_intake.sanitize_and_store(_jpeg(20, 10), dest_dir=tmp_path)
    assert Image.open(r["path"]).size == (20, 10)


def test_exif_not_carried_into_stored_file(tmp_path):
    """Тег после сохранения отсутствовать обязан: повернули — значит он больше не нужен,
    а второе применение перевернуло бы картинку ещё раз."""
    r = media_intake.sanitize_and_store(_jpeg(20, 10, orientation=6), dest_dir=tmp_path)
    assert not dict(Image.open(r["path"]).getexif())


# --- каталог назначения (новое) ------------------------------------------------

def test_dest_dir_overrides_default(tmp_path):
    inbox = tmp_path / "incoming"
    r = media_intake.sanitize_and_store(_png(), dest_dir=inbox)
    assert str(inbox) in r["path"] and (inbox).is_dir()


def test_dest_dir_created_if_missing(tmp_path):
    deep = tmp_path / "a" / "b" / "reports"
    media_intake.sanitize_and_store(_png(), dest_dir=deep)
    assert deep.is_dir()


def test_filename_is_sha256_of_original_bytes(tmp_path):
    import hashlib
    data = _png()
    r = media_intake.sanitize_and_store(data, dest_dir=tmp_path)
    assert r["sha256"] == hashlib.sha256(data).hexdigest()
    assert r["sha256"] in r["path"]


def test_same_bytes_deduped(tmp_path):
    data = _png()
    first = media_intake.sanitize_and_store(data, dest_dir=tmp_path)
    second = media_intake.sanitize_and_store(data, dest_dir=tmp_path)
    assert first["is_duplicate"] is False and second["is_duplicate"] is True
    assert first["path"] == second["path"]


# --- существующие защиты (характеризация) --------------------------------------

def test_non_image_rejected(tmp_path):
    with pytest.raises(media_intake.ImageRejected):
        media_intake.sanitize_and_store(b"%PDF-1.4 not an image", dest_dir=tmp_path)


def test_empty_rejected(tmp_path):
    with pytest.raises(media_intake.ImageRejected):
        media_intake.sanitize_and_store(b"", dest_dir=tmp_path)


def test_pixel_bomb_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(media_intake, "MAX_PIXELS", 100)
    with pytest.raises(media_intake.ImageRejected):
        media_intake.sanitize_and_store(_png(50, 50), dest_dir=tmp_path)


def test_oversized_bytes_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(media_intake, "MAX_BYTES", 10)
    with pytest.raises(media_intake.ImageRejected):
        media_intake.sanitize_and_store(_png(), dest_dir=tmp_path)
