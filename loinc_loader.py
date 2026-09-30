#!/usr/bin/env python3.11
"""loinc_loader.py — загрузка справочника LOINC в канон.

LOINC служит общим справочником имени аналита. Независимые словари
могут расходиться, поэтому сопоставления должны иметь единый источник.

Ключ двухуровневый:
  · `loinc_num` различает измерения, в том числе MCnc и SCnc;
  · `component` объединяет совместимые шкалы после конверсии.
Разные размерности разводятся, совместимые единицы приводятся.

ГРАНУЛЯРНОСТЬ ПЕРЕСМАТРИВАЛАСЬ ДВАЖДЫ, оба раза замером, оба раза в сторону
расширения — и это само по себе показание: «заказываемое» было плохой опорой.
  1. Одного заказного набора (1519) не хватило: обе его позиции B12 — `MCnc`,
     а `SCnc`-вариант `14685-2` (тот самый pmol/L) в набор не входит. Взяли все
     варианты заказываемых ВЕЩЕСТВ: 982 вещества → 10 732 строки.
  2. Не хватило и этого. `LoincUniversalLabOrdersValueSet` — список заказываемых
     ТЕСТОВ, а ОАК заказывается ОДНОЙ панелью: ни `lymphocyt`, ни `monocyt` в
     нём нет вовсе. Дом имени структурно не вмещал лейкоформулу. Берём ВСЕ
     действующие лабораторные термины: 60 009 (см. `_is_lab_term` — там замер).
`in_universal_order` остаётся КОЛОНКОЙ, но перестаёт быть ситом: «это заказывают»
— признак частоты, и слой сопоставления применяет его последним тай-брейком.

ВЕРСИЯ обязательна: LOINC выходит дважды в год, `MapTo.csv` переносит устаревшие
коды. Без записанной версии ссылки протухнут беззвучно — тот же класс, что 231
отказ вотчера без читателя.

ЛИЦЕНЗИЯ: LOINC распространяется с требованием атрибуции; владелец проверил условия
лично 2026-07-29. Текст атрибуции — `docs/reference/loinc_attribution.md`, его
наличие стережёт тест.

Публичный вход один: `load_loinc()`.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import health_db

log = logging.getLogger("loinc_loader")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ref.loinc_terms (
    loinc_num          TEXT PRIMARY KEY,
    component          TEXT,
    property           TEXT,
    system             TEXT,
    scale_typ          TEXT,
    long_common_name   TEXT,
    shortname          TEXT,
    status             TEXT,
    example_units      TEXT,
    in_universal_order INTEGER NOT NULL DEFAULT 0,
    loinc_version      TEXT NOT NULL,
    loaded_at          TEXT DEFAULT (datetime('now'))
)
"""

# Синонимы живут в каноне, а не в распакованных CSV: сопоставитель обязан быть
# воспроизводим через полгода, когда /tmp давно пуст, а архив релиза лежит где-то
# в загрузках. `source` хранится, чтобы было видно, откуда имя — из COMPONENT,
# из официальных RELATEDNAMES2 или из русского перевода.
_SCHEMA_SYN = """
CREATE TABLE IF NOT EXISTS ref.loinc_synonyms (
    loinc_num  TEXT NOT NULL,
    synonym    TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (loinc_num, synonym, source)
)
"""


# Русский лингвистический вариант релиза — ОТДЕЛЬНАЯ таблица, а не колонки в
# loinc_terms: это перевод, у него своя полнота и свой источник. Замер 2026-07-29:
# длинного русского имени (LONG_COMMON_NAME) нет НИ У ОДНОГО кода — пусто у всех
# 56 925 строк, — но части (компонент, материал, свойство, метод) переведены у
# 460 из 485 кодов нашего листа. Значит русское описание СОБИРАЕТСЯ из частей
# официального перевода, а не переводится нами: клинические имена — не место для
# отсебятины, «свободный T4» и «общий T4» отличаются одним словом.
_SCHEMA_RU = """
CREATE TABLE IF NOT EXISTS ref.loinc_ru (
    loinc_num  TEXT PRIMARY KEY,
    component  TEXT,
    system     TEXT,
    property   TEXT,
    method     TEXT
)
"""


def _norm_name(s: str) -> str:
    """Имя к сравнимому виду. Знаки препинания снимаются вторым ключом, а не
    вместо первого: «CA19-9» должно находиться и как «ca19 9», и как «ca199»."""
    s = (s or "").lower().replace("_", " ").replace("-", " ").replace(",", " ")
    return re.sub(r"\s+", " ", s.replace("ё", "е")).strip()


def _synonyms_of(num: str, row: dict, source: str = "en") -> list[tuple]:
    """Все имена кода из одной строки релиза → строки для loinc_synonyms.

    Берутся COMPONENT, первый токен SHORTNAME (короткие имена LOINC строятся как
    «ALP SerPl-cCnc», и первый токен — аббревиатура аналита), официальные
    RELATEDNAMES2 и потребительское имя. Замер 2026-07-29: этот набор насыщается —
    PartFile поверх него дал ровно ноль прироста, повторно его подключать не надо.
    """
    out = []
    seen = set()

    def put(val, kind):
        n = _norm_name(val)
        for k in (n, re.sub(r"[^a-zа-я0-9]", "", n)):
            if k and (k, kind) not in seen:
                seen.add((k, kind))
                out.append((num, k, f"{source}:{kind}"))

    comp = row.get("COMPONENT") or ""
    put(comp, "component")
    # У отношения «A/B» аналит — ЧИСЛИТЕЛЬ. Без отдельного ключа на него
    # `Neutrophils/Leukocytes` не находится по имени «Neutrophils», и на группе
    # процентов кандидатами шли антитела (наблюдалось 2026-07-29 в листе выбора).
    if "/" in comp:
        put(comp.split("/", 1)[0], "numerator")
    sn = _norm_name(row.get("SHORTNAME"))
    if sn:
        put(sn.split(" ")[0], "shortname")
    for s in (row.get("RELATEDNAMES2") or "").split(";"):
        put(s, "related")
    put(row.get("CONSUMER_NAME"), "consumer")
    return out


def _is_lab_term(row: dict) -> bool:
    """Берём ВСЕ действующие лабораторные термины (CLASSTYPE=1, STATUS=ACTIVE).

    Прежний отбор — «все варианты ВЕЩЕСТВ из Universal Lab Orders» — опровергнут
    данными 2026-07-29. `LoincUniversalLabOrdersValueSet` — список ЗАКАЗЫВАЕМЫХ
    ТЕСТОВ, а ОАК заказывается ОДНОЙ панелью, поэтому его аналиты-результаты в
    этом списке отсутствуют начисто: grep по нему не находит ни `lymphocyt`, ни
    `monocyt`. `Neutrophils` попал в отбор СЛУЧАЙНО — через строку «Neutrophils
    [Presence] in Stool by Wright stain». То есть дом имени структурно не мог
    вместить нужные варианты лейкоформулы.

    Выбор более полного лабораторного справочника уменьшает риск ложной
    однозначности: единственный кандидат может оказаться единственным лишь
    потому, что правильный код отсутствует в урезанном наборе.
    Цена: справочник в каноне ≈ 21 МБ → ≈ 110 МБ.
    """
    return row.get("CLASSTYPE") == "1" and row.get("STATUS") == "ACTIVE"


def _universal_order_codes(csv_dir: Path) -> set[str]:
    """Коды из Universal Lab Orders — та самая гранулярность «заказываемое»."""
    path = csv_dir / "LoincUniversalLabOrdersValueSet.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["LOINC_NUM"] for r in csv.DictReader(fh) if r.get("LOINC_NUM")}


def load_loinc(csv_dir: str | Path, version: str) -> dict:
    """Загружает подмножество LOINC в `loinc_terms`. Идемпотентна (REPLACE).

    csv_dir — каталог с `LoincTableCore.csv` и `LoincUniversalLabOrdersValueSet.csv`.
    version — версия релиза («2.82»); хранится в каждой строке, потому что смена
    версии обязана быть видимой, а не выводимой из даты загрузки.

    Возвращает {'universal', 'components', 'loaded', 'version', 'missing_in_core'}.
    `loaded` БОЛЬШЕ `universal`: грузятся все варианты заказываемых веществ.
    `missing_in_core` — коды заказного набора, которых нет в ядре: они считаются, а
    не пропадают молча, потому что тихая потеря строки справочника — дыра, которую
    потом не найти.
    """
    csv_dir = Path(csv_dir)
    # ПОЛНАЯ таблица предпочтительнее ядра: только в ней есть RELATEDNAMES2 и
    # CONSUMER_NAME — официальные синонимы, ради которых синонимы и заводятся.
    # Проверено 2026-07-29: загрузка из LoincTableCore дала en:related = 0, то есть
    # индекс в каноне был слабее того, на котором мерился потолок сопоставления.
    core = csv_dir / "Loinc.csv"
    if not core.exists():
        core = csv_dir / "LoincTableCore.csv"
        log.warning("полная Loinc.csv не найдена, беру ядро — синонимов "
                    "RELATEDNAMES2/CONSUMER_NAME не будет")
    wanted = _universal_order_codes(csv_dir)

    # Один проход по ядру: берём все действующие лабораторные термины. `wanted`
    # больше не ОТБИРАЕТ — он только ПОМЕЧАЕТ (`in_universal_order`), потому что
    # «это заказывают» оказалось признаком частоты, а не правильности, и в роли
    # сита выбрасывало верные коды (см. `_is_lab_term`).
    components, seen = set(), set()
    rows, syns = [], []
    with core.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("LOINC_NUM") in wanted:
                seen.add(r["LOINC_NUM"])
            if not _is_lab_term(r):
                continue
            if r.get("COMPONENT"):
                components.add(r["COMPONENT"])
            num = r["LOINC_NUM"]
            rows.append((num, r.get("COMPONENT"), r.get("PROPERTY"),
                         r.get("SYSTEM"), r.get("SCALE_TYP"), r.get("LONG_COMMON_NAME"),
                         r.get("SHORTNAME"), r.get("STATUS"),
                         r.get("EXAMPLE_UCUM_UNITS"),
                         1 if num in wanted else 0, version))
            syns.extend(_synonyms_of(num, r))

    # Русский перевод дополняет поиск по сырым написаниям;
    # канонические имена и текст лаборатории могут различаться.
    ru = csv_dir / "ruRU20LinguisticVariant.csv"
    ru_rows = []
    if ru.exists():
        loaded_nums = {r[0] for r in rows}
        with ru.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["LOINC_NUM"] in loaded_nums:
                    syns.extend(_synonyms_of(r["LOINC_NUM"], r, source="ru"))
                    ru_rows.append((r["LOINC_NUM"], r.get("COMPONENT"),
                                    r.get("SYSTEM"), r.get("PROPERTY"),
                                    r.get("METHOD_TYP")))

    with health_db.get_conn() as conn:
        # Справочник живёт в ОТДЕЛЬНОМ общем файле, не в каноне: см. комментарий
        # у health_db.attach_reference. Схемы создаются в `ref` явно — иначе
        # CREATE попал бы в main и завёл вторую, невидимую копию.
        health_db.attach_reference(conn)
        conn.execute(_SCHEMA)
        conn.execute(_SCHEMA_SYN)
        conn.execute(_SCHEMA_RU)
        conn.execute("DELETE FROM ref.loinc_ru")
        # Справочник заменяется ЦЕЛИКОМ, а не дописывается. Иначе строки, попавшие
        # по прежнему правилу отбора, живут дальше молча: правило сменилось —
        # они уже не «то, что мы решили держать», но кандидатами предлагаются.
        # Ровно это и случилось бы сейчас: отбор «варианты заказываемых веществ»
        # тянул и снятые с употребления коды, которых новый отбор не берёт.
        conn.execute("DELETE FROM ref.loinc_synonyms")
        conn.execute("DELETE FROM ref.loinc_terms")
        conn.executemany(
            "INSERT OR REPLACE INTO ref.loinc_terms (loinc_num, component, property, "
            "system, scale_typ, long_common_name, shortname, status, "
            "example_units, in_universal_order, loinc_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.executemany(
            "INSERT OR REPLACE INTO ref.loinc_synonyms (loinc_num, synonym, source) "
            "VALUES (?,?,?)", syns)
        conn.executemany(
            "INSERT OR REPLACE INTO ref.loinc_ru (loinc_num, component, system, "
            "property, method) VALUES (?,?,?,?,?)", ru_rows)
        conn.commit()
    missing = wanted - seen
    if missing:
        log.warning(f"кодов заказного набора нет в ядре: {len(missing)} "
                    f"(пример: {sorted(missing)[:3]})")
    return {"universal": len(wanted), "components": len(components),
            "loaded": len(rows), "version": version, "missing_in_core": len(missing),
            "ru": len(ru_rows)}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", required=True)
    ap.add_argument("--version", required=True)
    a = ap.parse_args()
    print(json.dumps(load_loinc(a.csv_dir, a.version), ensure_ascii=False, indent=2))
