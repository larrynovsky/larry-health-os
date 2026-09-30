"""
tests/unit/test_save_clinical_idempotence.py — характеризация A9 (точка ветвления).

Нить: ремонт `data_ingestion`, Фаза A. Baseline `e4617ff`.

ЗАЧЕМ. Решение 2 ремонта — «побочные эффекты объявляются независимыми, откат не
вводим». Оно работает ТОЛЬКО если `save_clinical_to_db` идемпотентна: при провале
staging документ не помечается импортированным, значит повтор неизбежен, и второй
проход не должен плодить консультации.

РЕЗУЛЬТАТ ХАРАКТЕРИЗАЦИИ (ожидаемый на baseline):
  A9.1  ЗЕЛЁНЫЙ — идемпотентность есть: дубль ловится по `(date, specialist_type)`
        (`import_all.py:432-438`). Решение 2 остаётся в силе.
  A9.2  КРАСНЫЙ — ключ дубля НЕ включает `source_file`. Два РАЗНЫХ документа одного
        типа за одну дату схлопываются: второй молча не сохраняется. Два визита к
        онкологу в один день — нормальный случай, не экзотика.
  A9.3  КРАСНЫЙ — отказ записи гасится `except Exception: print(...)`
        (`import_all.py:448-449`). Вызывающий не узнаёт, что консультация не сохранена.
        Это F-03 внутри функции: ошибка есть, статус её не несёт.

ГРАНИЦА. Здесь не проверяется, что консультация КЛИНИЧЕСКИ верна — только что запись
происходит ровно один раз и что отказ виден. Содержание — не машинный оракул.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

import import_all as mod


CLINICAL = next(iter(mod.CLINICAL_TYPES))          # любой тип из живого словаря
DATE = "2026-01-15"


@pytest.fixture
def health_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "HEALTH", tmp_path)
    return tmp_path


def _doc(health_root, name):
    p = health_root / name
    p.touch()
    return p


def test_a9_1_repeat_same_document_saves_once(db, health_root):
    """A9.1 — повтор по тому же документу не плодит консультацию. Ожидается ЗЕЛЁНЫМ.

    Это и есть условие решения 2: при провале staging документ не помечается
    импортированным, повтор гарантирован, и он обязан быть безвредным.
    """
    doc = _doc(health_root, "visit.pdf")
    with mock.patch.object(mod.db, "save_consultation", wraps=mod.db.save_consultation) as save:
        mod.save_clinical_to_db(CLINICAL, DATE, doc, {"summary_text": "x"})
        mod.save_clinical_to_db(CLINICAL, DATE, doc, {"summary_text": "x"})
    assert save.call_count == 1, (
        f"повтор создал {save.call_count} записей — решение 2 (эффекты независимы, "
        "откат не вводим) на этом основании работать не может"
    )


@pytest.mark.xfail(strict=True, reason=(
    "F-19: ключ дубля консультаций не включает `source_file` — два РАЗНЫХ документа одного "
    "типа за одну дату схлопываются в один. Зелёным станет в Фазе C."))
def test_a9_2_different_documents_same_day_both_saved(db, health_root):
    """A9.2 — два РАЗНЫХ документа одного типа за одну дату сохраняются оба.

    Ключ дубля — `(date, specialist_type)`, без `source_file`. Поэтому второй визит
    к тому же специалисту в тот же день считается повтором первого и теряется молча.
    Два приёма в один день — обычная ситуация, а не край.

    Ошибка усиливается F-12: `date` не имеет объявленной семантики, поэтому «один
    день» может означать один день забора, исполнения или выдачи — то есть склеивать
    документы, разнесённые во времени.
    """
    a = _doc(health_root, "visit_a.pdf")
    b = _doc(health_root, "visit_b.pdf")
    with mock.patch.object(mod.db, "save_consultation", wraps=mod.db.save_consultation) as save:
        mod.save_clinical_to_db(CLINICAL, DATE, a, {"summary_text": "первый"})
        mod.save_clinical_to_db(CLINICAL, DATE, b, {"summary_text": "второй"})
    assert save.call_count == 2, (
        f"сохранено {save.call_count} из 2 документов — второй документ того же типа "
        "за ту же дату потерян, потому что ключ дубля не различает источник"
    )


@pytest.mark.xfail(strict=True, reason=(
    "F-03 внутри функции: отказ записи гаснет в `except Exception: print(...)` и наружу "
    "уходит None — вызывающий не может отразить его в коде возврата. Зелёным — в Фазе C."))
def test_a9_3_save_failure_is_visible_to_caller(db, health_root):
    """A9.3 — отказ записи виден вызывающему, а не только в stdout.

    Сейчас `except Exception as e: print(...)` (`import_all.py:448-449`) гасит любой
    отказ БД. Функция возвращает `None` и при успехе, и при провале, поэтому
    `main()` не может учесть его в коде возврата. Это F-03 внутри одной функции:
    ошибка произошла, была напечатана и не стала статусом.

    Контракт: либо исключение наружу, либо явный результат. `None` в обоих случаях —
    не контракт.
    """
    doc = _doc(health_root, "visit_fail.pdf")
    with mock.patch.object(mod.db, "save_consultation", side_effect=RuntimeError("planted: db down")):
        result = mod.save_clinical_to_db(CLINICAL, DATE, doc, {"summary_text": "x"})
    assert result is not None, (
        "провал записи вернул None — неотличимо от успешного сохранения; "
        "вызывающий не может отразить это в коде возврата"
    )
