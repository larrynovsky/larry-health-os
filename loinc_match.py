#!/usr/bin/env python3.11
"""loinc_match.py — сопоставление наших имён аналитов с кодами LOINC.

Что здесь есть и чего нет — важнее, чем как это устроено.

ЕСТЬ: детерминированный каскад фильтров. Кандидаты ищутся по именам (наши
синонимы как мост + синонимы LOINC из канона), затем сужаются свойством,
материалом и единицей. Каждый фильтр — либо данные LOINC, либо наши собственные
соглашения об именовании, которые мы знаем и контролируем.

НЕТ: нечёткого сравнения строк. Для клинических имён неверифицируемое совпадение
хуже отсутствия — `T4 free` и `T4 total` отличаются одним словом, и такая ошибка
не покраснеет ни в одном тесте: её увидит только владелец, возможно, через год
в тренде. Поэтому неоднозначность НЕ разрешается «наиболее похожим», а честно
возвращается человеку списком.

Неполный справочник может создавать ложную однозначность: единственный
кандидат не обязательно верен, если правильного кода нет в наборе.
Оценивать нужно также непокрытые пары, а не только долю авто-сопоставлений.

Публичные входы: `propose_mappings()` — что машина ставит сама, что отдаёт человеку;
`render_worksheet()` — тот же ответ в форме, пригодной для вынесения вердикта;
`record_mapping()` — записать решение с провенансом.
"""
from __future__ import annotations

import logging
import pathlib
import re
from collections import defaultdict

import health_db
import lab_canon
import lab_promote

log = logging.getLogger("loinc_match")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_name_loinc (
    our_name    TEXT NOT NULL,
    unit        TEXT,
    specimen    TEXT NOT NULL DEFAULT 'blood',
    loinc_num   TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    rule        TEXT,
    decided_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (our_name, unit, specimen)
)
"""

# Материал — часть ключа. Одно имя и единица в разных образцах
# не доказывают тождество измерения. Правило вывода материала
# используется из lab_promote, а не дублируется.

# Материалы, которые для нас «кровь». Список неполон по построению: неучтённая
# система уводит кандидата в «спорно», а не в ошибку — сторона безопасная.
_BLOOD = {"ser/plas", "ser", "plas", "bld", "ser/plas/bld", "bld/tiss",
          "ser/plas/urine", "plas/bld"}

# Наши суффиксы несут СВОЙСТВО измерения: «_pct» — доля, «_abs» — концентрация
# числа. Это наша конвенция, а не догадка о LOINC.
_SUFFIX_PROPERTY = {"_pct": "nfr", "_abs": "ncnc"}


def _norm(s: str) -> str:
    s = (s or "").lower().replace("_", " ").replace("-", " ").replace(",", " ")
    return re.sub(r"\s+", " ", s.replace("ё", "е")).strip()


def _unit_key(u: str) -> str:
    return re.sub(r"\s+", "", lab_canon.norm_unit(u or "").lower())


def decompose(name: str) -> tuple[set[str], str | None, str | None]:
    """Наше имя → (ключи поиска, требуемое свойство, требуемый материал).

    Суффикс `_pct`/`_abs` и префикс `Urine_` несут уже известные ограничения.
    `lab_canon.synonyms_for` дополняет поисковые формы общего справочника.
    """
    prop = system = None
    base = name
    for suf, p in _SUFFIX_PROPERTY.items():
        if base.endswith(suf):
            base, prop = base[: -len(suf)], p
            break
    if base.startswith("Urine_"):
        base, system = base[6:], "urine"
    keys = {_norm(base)} | {_norm(x) for x in lab_canon.synonyms_for(base)}
    keys |= {_norm(x) for x in lab_canon.synonyms_for(name)}
    keys |= {re.sub(r"[^a-zа-я0-9]", "", k) for k in set(keys)}
    return {k for k in keys if k}, prop, system


# Источники имён делятся на ТОЧНЫЕ и ШИРОКИЕ, и это различие несёт вес.
# COMPONENT/числитель/аббревиатура из SHORTNAME называют САМ аналит. RELATEDNAMES2
# и потребительское имя называют всё, что рядом: у `CD19 cells/Lymphocytes`
# в related есть «Lymphocytes», и по широкому индексу `Lymphocytes_pct` собирал
# 33 кандидата вместо 4 (замер 2026-07-29). Но выбросить широкие нельзя — без них
# непокрытых пар 13 → 46. Поэтому не «или», а ОЧЕРЁДНОСТЬ: широкие спрашиваются,
# только когда точные не дали ничего.
_PRECISE_SOURCES = {"component", "numerator", "shortname"}


def _load_index(conn):
    """Индекс из КАНОНА, не из CSV: сопоставитель обязан быть воспроизводим
    тогда, когда распакованного релиза давно нет.

    Возвращает (meta, tight, loose) — два индекса имён, см. `_PRECISE_SOURCES`."""
    meta, tight, loose = {}, defaultdict(set), defaultdict(set)
    for num, order, system, scale, prop, units, comp, long in conn.execute(
            "SELECT loinc_num, in_universal_order, system, scale_typ, property, "
            "example_units, component, long_common_name FROM loinc_terms"):
        meta[num] = {"ordered": order, "system": (system or "").lower(),
                     "scale": (scale or "").lower(), "property": (prop or "").lower(),
                     "component": comp or "", "long": long or "",
                     "units": {_unit_key(u) for u in (units or "").split(";") if u.strip()}}
    for num, syn, src in conn.execute(
            "SELECT loinc_num, synonym, source FROM loinc_synonyms"):
        if num in meta:
            idx = tight if src.split(":")[-1] in _PRECISE_SOURCES else loose
            idx[syn].add(num)
    return meta, tight, loose


def resolve_loinc(name: str, unit: str, meta: dict, tight: dict, loose: dict) -> set[str]:
    """Каскад по точным именам, и только при пустом результате — по широким."""
    return candidates(name, unit, meta, tight) or candidates(name, unit, meta, loose)


# ─────────── клинически невозможные кандидаты ───────────
# Отсев по ФАКТАМ О ЧЕЛОВЕКЕ, а не по правдоподобию. Факты берутся из канона
# (`patient_profile`), НИКОГДА из кода: §9 — клинические данные не живут в
# репозитории, код знает только РАСПОЛОЖЕНИЕ. Поэтому здесь нет ни возраста,
# ни пола, ни имени — только правила, которые эти значения применяют.
#
# Каждое отсеянное ПОКАЗЫВАЕТСЯ человеку с причиной, а не исчезает. Неверный
# факт (ошибка в дате рождения, изменившийся распорядок) иначе спрятал бы верный
# ответ молча — а молчаливое сужение и есть тот класс, который никто не заметит.

# Материалы новорождённого: `RBC^BldCo` — пуповинная кровь, `RBC^Fetus` — плод.
# Замер 2026-07-29: таких кодов в листе 7.
_NEWBORN_SYSTEM_MARKERS = ("^bldco", "^fetus", "^neonat")

# Метка после `^` в COMPONENT — это «challenge» LOINC: УСЛОВИЕ ЗАБОРА, отличное
# от рутинного. Замер по кандидатам листа 2026-07-29 дал 531 такой код: нагрузки
# (`2H post 75 g glucose PO`), еда (`1H post meal`), время суток (`8 AM specimen`,
# `12.00 specimen`), диализ, лекарственные пробы. Правило написано на НАЛИЧИИ
# метки, а не на слове «post»: первая редакция ловила только post/pre и пропускала
# суточный гликемический профиль — двенадцать «Глюкоза^N.00 образец» доехали до
# листа и были видны глазами в выдаче.
#
# Исключение ровно одно: `CFst` (carbohydrate fast) — это и ЕСТЬ натощак.
_FASTING_MARKER = "cfst"


def clinical_facts() -> dict:
    """Факты о человеке ИЗ КАНОНА, пригодные для отсева кандидатов.

    Читает через `profile_db.get_patient_profile` — дом профиля один, и лезть в
    `patient_profile` своим SELECT значило бы завести второй. Дубль-гейт по
    ДАННЫМ поймал ровно это 2026-07-29, и он был прав.

    Возвращает {'adult': bool|None, 'fasting_labs': bool|None}. `None` означает
    «факта нет» — и тогда соответствующее правило молчит. Отсутствие факта не
    приравнивается к «нет» намеренно: отсеивать по незнанию хуже, чем не отсеять.
    """
    import profile_db
    prof = profile_db.get_patient_profile()
    facts = {"adult": None, "fasting_labs": None}
    born = (prof.get("identity.birth_date") or "").strip()[:4]
    if born.isdigit():
        from _time_inject import get_today
        facts["adult"] = get_today().year - int(born) >= 18
    fl = (prof.get("routine.fasting_labs") or "").strip().lower()
    if fl in ("true", "false"):
        facts["fasting_labs"] = fl == "true"
    return facts


def impossible_for(code: str, meta: dict, facts: dict) -> str | None:
    """Причина, по которой код невозможен для этого человека, или None.

    Правило применяется, ТОЛЬКО если соответствующий факт известен. Причина
    возвращается текстом, потому что она предъявляется человеку — «отсеяно 3»
    без «почему» проверить нельзя, а непроверяемое сужение равно догадке.
    """
    m = meta.get(code) or {}
    sysm = (m.get("system") or "").lower()
    comp = (m.get("component") or "").lower()
    if facts.get("adult") and any(k in sysm for k in _NEWBORN_SYSTEM_MARKERS):
        return "материал новорождённого (пуповинная кровь или плод)"
    if facts.get("fasting_labs") and "^" in comp:
        mark = comp.split("^", 1)[1].strip()
        if _FASTING_MARKER not in mark:
            return f"особое условие забора «{mark}» — у тебя всегда натощак"
    return None


def _generic_pick(pool: set[str], meta: dict) -> str | None:
    """Единственный код без указания МЕТОДА среди кандидатов, различающихся ТОЛЬКО
    методом. Иначе None.

    Почему это законная автоматика, а не «выбрать похожего»: ключ тренда —
    `component`, и когда у всех кандидатов совпадают и `component`, и материал,
    выбор между «by Automated count» и «by Manual count» физически не может
    сдвинуть тренд. Различие материала (`Bilirubin.total` в Serum против Blood)
    или компонента — может, поэтому там автоматика молчит.

    Замер 2026-07-29: из 169 выборов 82 отличаются только методом; в 56 из них
    ровно один вариант без метода. Остальные 26 упираются в материал (Cord blood,
    Fetus) и уходят человеку — правильно уходят.
    """
    if len(pool) < 2:
        return None
    if len({(meta[p]["component"], meta[p]["system"]) for p in pool}) != 1:
        return None
    plain = [p for p in pool if " by " not in meta[p]["long"]]
    return plain[0] if len(plain) == 1 else None


def candidates(name: str, unit: str, meta: dict, by_name: dict) -> set[str]:
    """Кандидаты после каскада. Каждый фильтр применяется, только если не
    обнуляет набор: сузить до пустоты хуже, чем оставить выбор человеку."""
    keys, prop, system = decompose(name)
    pool = set()
    for k in keys:
        pool |= by_name.get(k, set())
    if not pool:
        return pool
    # ПОРЯДОК ФИЛЬТРОВ ЗНАЧИМ: смысл раньше популярности. «Заказной» — признак
    # частоты, а не правильности, и когда он стоял первым, он выбрасывал верные
    # коды до всякой проверки смысла: у `Neutrophils_pct` в заказном наборе есть
    # АНТИТЕЛА к нейтрофилам, а доля `Neutrophils/Leukocytes` — нет, и лист выбора
    # предлагал антитела (наблюдалось 2026-07-29 глазами в самом листе).
    # Поэтому «заказной» применяется последним — как тай-брейк, а не как отсев.
    if prop:
        pool = {p for p in pool if meta[p]["property"] == prop} or pool
    else:
        pool = {p for p in pool if meta[p]["scale"] == "qn"} or pool
    want = {"urine"} if system == "urine" else _BLOOD
    pool = {p for p in pool if meta[p]["system"] in want} or pool
    # Единица — последний и самый узкий фильтр. Опирается на EXAMPLE_UCUM_UNITS
    # самого LOINC, то есть на его данные, а не на нашу догадку. Заполнена она у
    # 51% кодов — там, где пусто, фильтр молча отступает (в этом и смысл `or pool`).
    uk = _unit_key(unit)
    pool = {p for p in pool if uk in meta[p]["units"]} or pool
    # «Заказной набор» — тай-брейк последним: среди уже осмысленно суженных
    # кандидатов частота действительно полезный сигнал. Раньше по каскаду —
    # вредный (см. комментарий выше).
    pool = {p for p in pool if meta[p]["ordered"]} or pool
    return pool


def worksheet_state(res: dict) -> dict:
    """Что страница обязана сказать человеку про своё состояние.

    Решение вынесено ИЗ разметки в Python сознательно: пустое состояние — это
    суждение («работы нет, вот что осталось и почему»), а не оформление, и
    суждение обязано быть проверяемым без браузера. Разметка была не покрыта
    тестом, и это стоило ровно того, чего и должно было: 2026-07-30 владелец
    ответил на все вопросы, открыл пересобранный лист и увидел ПУСТУЮ страницу —
    заголовок «Выбери код (0)» и четыре свёрнутых раздела. Ни ошибки, ни объяснения.

    Возвращает {'headline', 'hint', 'open_missing'}.
    """
    n_ch = len(res.get("choices") or [])
    n_au = len(res.get("audit") or [])
    n_ms = len(res.get("missing") or [])
    if n_ch or n_au:
        return {"headline": f"Выбери код ({n_ch})", "hint": "", "open_missing": False}
    hint = ("Все пары, до которых машина дотянулась именем, решены — "
            f"их {len(res.get('decided') or [])}, ещё {len(res.get('auto') or [])} "
            "машина поставила сама (раздел ниже, стоит просмотреть).")
    if n_ms:
        hint += (f" Осталось {n_ms} имён, до которых мост имён LOINC не дотягивается "
                 "вовсе: там нужен не выбор из вариантов, а твоё слово о том, что это "
                 "за анализ. Они раскрыты ниже.")
    return {"headline": "Выбирать нечего", "hint": hint, "open_missing": bool(n_ms)}


def _measurement_weight(rows):
    """Вес пары — число РАЗЛИЧИМЫХ ИЗМЕРЕНИЙ, а не строк таблицы.

    Повторные разборы документа создают несколько строк одного измерения.
    Вес по строкам завысил бы частоту и изменил порядок вопросов человеку:
    чаще спрашивалось бы о повторно разобранном, а не чаще измеренном.

    Измерение различается по (документ, дата, каноническое имя, сырая единица).
    Чистая функция ради оракула: вход — строки staging, выход — (вес, написания).
    """
    weight, raw_units, seen = defaultdict(int), defaultdict(set), set()
    for rn, n, u, p, src, date in rows:
        name = lab_promote.canon_of({"raw_name": rn, "canonical_name": n})
        key = (name, _conv_unit(name, u),
               lab_promote.specimen_of({"panel": p, "canonical_name": name}))
        raw_units[key].add(u)          # написания копим по ВСЕМ строкам: они и есть
        fp = (src, date, name, u)      # предмет вопроса «одна единица или разные»
        if fp in seen:
            continue
        seen.add(fp)
        weight[key] += 1
    return weight, raw_units


def _conv_unit(name: str, unit: str) -> str:
    """Единица пары — КОНВЕНЦИОНАЛЬНАЯ, а не та, что напечатана в строке.

    Общая шкала связывает вопрос с ранее принятым отображением независимо
    от исходной единицы и предотвращает повторные вопросы о той же паре.
    Приведение использует `lab_canon`, как и читатель тренда.

    У безразмерного аналита единица не входит в ключ: ошибочная подпись
    единицы на бланке не меняет природу отношения величин.
    """
    if lab_canon.is_dimensionless(name):
        return ""
    _v, conv = lab_canon.to_conventional(name, 1.0, unit or "")
    return lab_canon.norm_unit(conv or unit or "")


def propose_mappings() -> dict:
    """Разложить живые пары (имя, единица) на три корзины.

    Возвращает {'auto': [...], 'choices': [...], 'missing': [...]}; choices
    и missing упорядочены по весу различимых измерений за парой, чтобы
    первыми решениями закрывать больший объём.
    """
    facts = clinical_facts()
    with health_db.get_conn() as conn:
        health_db.attach_reference(conn)
        conn.execute(_SCHEMA)
        meta, tight, loose = _load_index(conn)
        # Ключ пары — имя и нормализованная единица: письменность не создаёт
        # нового вопроса. Имя выводится правилом lab_promote.canon_of,
        # поскольку сохранённый canonical_name может быть устаревшим снимком.
        weight, raw_units = _measurement_weight(conn.execute(
            "SELECT raw_name, canonical_name, unit, panel, source_file, date "
            "FROM lab_results_staging "
            "WHERE canonical_name IS NOT NULL AND canonical_name <> '' "
            "AND unit IS NOT NULL AND unit <> ''"))
        # Уже решённое человеком не спрашивается второй раз. Без этого лист
        # каждый раз выкладывал бы обратно те же пары, и вердикт владельца
        # выглядел бы как несделанная работа.
        # Вместе с кодом читаем ПРОВЕНАНС и улику. Решение, записанное агентом по
        # бланку, — не то же самое, что вердикт владельца: первое обязано быть
        # предъявлено ему на проверку, второе спрашивать второй раз нельзя.
        decided = {(r[0], _conv_unit(r[0], r[1]), r[2]): (r[3], r[4], r[5])
                   for r in conn.execute(
                       "SELECT our_name, unit, specimen, loinc_num, decided_by, rule "
                       "FROM lab_name_loinc")}

    auto, choices, missing, done, audit = [], [], [], [], []
    for (name, unit, spec), rows in weight.items():
        base = {"name": name, "unit": unit, "specimen": spec, "rows": rows,
                "raw_units": sorted(raw_units[(name, unit, spec)])}
        if (name, unit, spec) in decided:
            code, by, rule = decided[(name, unit, spec)]
            item = {**base, "loinc": code, "by": by, "rule": rule}
            # Решения агента идут в ОТДЕЛЬНУЮ корзину: их надо проверить, а не
            # спрятать в «уже решено». Спрятать значило бы выдать свою догадку за
            # вердикт владельца — ровно та подмена, из-за которой поле
            # `decided_by` вообще существует.
            (audit if by != "owner" else done).append(item)
            continue
        # Материал из нашей строки — тоже фильтр: мочевой аналит не должен
        # предлагать сывороточные коды. Префикс `Urine_` делал это и раньше,
        # но только для имён, которые распознаватель успел так назвать.
        lookup = name if (spec != "urine" or name.startswith("Urine_")) else f"Urine_{name}"
        found = resolve_loinc(lookup, unit, meta, tight, loose)
        dropped = {c: r for c in found if (r := impossible_for(c, meta, facts))}
        # Отсев не имеет права ОБНУЛИТЬ выбор: если невозможным оказалось всё,
        # это скорее говорит о неверном факте, чем о правде про кандидатов.
        pool = ({c for c in found if c not in dropped} or found)
        if pool == found:
            dropped = {}
        gen = _generic_pick(pool, meta)
        item = {**base,
                "dropped": [{"loinc": c, "why": w} for c, w in sorted(dropped.items())]}
        if len(pool) == 1:
            auto.append({**item, "loinc": next(iter(pool)), "rule": "single-candidate"})
        elif gen:
            auto.append({**item, "loinc": gen, "rule": "generic-variant",
                         "over": sorted(pool)})
        elif pool:
            choices.append({**item, "options": sorted(pool)})
        else:
            missing.append(item)
    key = lambda d: -d["rows"]
    return {"auto": sorted(auto, key=key), "choices": sorted(choices, key=key),
            "missing": sorted(missing, key=key), "decided": sorted(done, key=key),
            "audit": sorted(audit, key=key), "facts": facts}


_WORKSHEET_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LOINC: лист выбора</title>
<style>
 :root{--bg:#fbfbfa;--fg:#1c1b19;--mut:#6b6862;--line:#e3e1dc;--card:#fff;
       --acc:#2f6f4f;--sel:#eef5f0}
 @media(prefers-color-scheme:dark){:root{--bg:#161513;--fg:#e9e7e2;--mut:#9b968d;
       --line:#2e2c28;--card:#1e1d1a;--acc:#7fb495;--sel:#22302a}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
 header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
        padding:14px 20px;z-index:5}
 h1{font-size:17px;margin:0 0 4px}
 .sub{color:var(--mut);font-size:13px}
 .bar{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
 button{font:inherit;padding:7px 13px;border:1px solid var(--line);border-radius:7px;
        background:var(--card);color:var(--fg);cursor:pointer}
 button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
 main{padding:16px 20px 60px;max-width:1000px;margin:0 auto}
 .item{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:14px 16px;margin-bottom:12px}
 .item.done{border-color:var(--acc)}
 .hd{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
 .nm{font-weight:600}
 .un{color:var(--mut);font-size:13px}
 .rows{color:var(--mut);font-size:12px;white-space:nowrap}
 .opts{margin-top:10px;display:grid;gap:6px}
 label.opt{display:grid;grid-template-columns:22px 1fr;gap:8px;padding:8px 10px;
       border:1px solid var(--line);border-radius:7px;cursor:pointer;align-items:start}
 label.opt:hover{background:var(--sel)}
 label.opt.on{background:var(--sel);border-color:var(--acc)}
 .code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
       color:var(--acc)}
 .meta{color:var(--mut);font-size:12px;display:block}
 .drop{margin:6px 0 0;font-size:12px;color:var(--mut)}
 .drop summary{cursor:pointer;padding:4px 0}
 .drop .meta{padding:2px 0 2px 14px}
 .tag{display:inline-block;font-size:11px;border:1px solid var(--acc);
      border-radius:4px;padding:0 5px;margin-left:6px;color:var(--acc)}
 details{margin-top:22px}
 summary{cursor:pointer;color:var(--mut);padding:6px 0}
 table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
 td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}
 th{color:var(--mut);font-weight:500}
 .out{width:100%;min-height:150px;font-family:ui-monospace,monospace;font-size:12px;
      margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:7px;
      background:var(--card);color:var(--fg)}
 .note{color:var(--mut);font-size:12px;margin:10px 0 0}
</style></head><body>
<header>
  <h1>LOINC — лист выбора</h1>
  <div class="sub" id="sub"></div>
  <div class="bar" id="bar">
    <button id="nextBtn">К следующей неразмеченной</button>
    <button id="exportBtn" class="primary">Выгрузить решения</button>
    <span class="rows" id="prog"></span>
  </div>
</header>
<main>
  <section id="auditWrap" hidden>
    <h2 style="font-size:15px;margin:6px 0 2px">Проверь мои решения по бланкам</h2>
    <p class="note" style="margin:0 0 12px">Я поставил их сам, читая документ.
    Провенанс в каноне — <code>agent_from_blank</code>, улика приведена под каждым.
    Пока ты не подтвердил, это моя догадка, а не твой вердикт.</p>
    <div id="audit"></div>
  </section>
  <h2 style="font-size:15px;margin:22px 0 8px" id="chHd">Выбери код</h2>
  <div id="list"></div>
  <details id="exp"><summary>Выгрузка решений (JSON)</summary>
    <textarea class="out" id="out" readonly></textarea>
    <p class="note">Скопируй и пришли — решения лягут в lab_name_loinc с провенансом
    owner. Страница ничего не сохраняет сама: её задача — помочь вынести вердикт,
    а домом вердикта остаётся канон.</p>
  </details>
  <details><summary id="autoSum"></summary><div id="auto"></div></details>
  <details id="missWrap"><summary id="missSum"></summary><div id="miss"></div></details>
  <details><summary id="doneSum"></summary><div id="done"></div></details>
</main>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
var DATA = JSON.parse(document.getElementById('payload').textContent);
var picks = {};
var verdicts = {};
var D = DATA.desc || {};
var SPEC = {blood:'кровь', urine:'моча', stool:'кал', saliva:'слюна', other:'материал не определён'};
function key(d){ return d.name + '|' + d.unit + '|' + (d.specimen || 'blood'); }
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function idOf(d){ return 'i' + DATA.choices.indexOf(d); }

// Размерность показывается явно; отсутствие перевода тоже обозначается.
// Неоднозначное название без этих подсказок может вызвать ложный отказ.
var PROP_RU = {entmeanvol:'средний объём одной клетки', entmcnc:'масса на объём клетки',
  entvol:'объём одной клетки', mcnc:'массовая концентрация', scnc:'вещественная концентрация',
  ncnc:'число в объёме', nfr:'доля от целого', acnc:'активность в объёме',
  'sedimentation rate':'скорость оседания', ccnc:'каталитическая активность',
  mrto:'молярное отношение', srto:'отношение', ent:'число'};
function dimHtml(m){
  var p = String(m.property || '').toLowerCase();
  var ru = PROP_RU[p];
  var bits = [ru ? ru + ' (' + m.property + ')' : m.property, m.units].filter(Boolean);
  var warn = m.ru ? '' : ' · <b>официального русского имени у этого кода нет</b>';
  return '<span class="meta">размерность: ' + esc(bits.join(' · ')) + warn + '</span>';
}

function optHtml(d, i, code){
  var m = D[code] || {};
  var ord = m.ordered ? '<span class="tag">заказной</span>' : '';
  // Сверху — официальный русский перевод LOINC, снизу мелким — английский
  // оригинал. Оригинал не убран: перевод собран из частей и у части кодов
  // отсутствует, а сверять решение по замазанному тексту нельзя.
  var head = m.ru || m.long || m.short;
  var sub  = m.ru ? (m.long || m.short) : '';
  return '<label class="opt" data-i="' + i + '" data-c="' + esc(code) + '">' +
    '<input type="radio" name="r' + i + '" value="' + esc(code) + '">' +
    '<span><span class="code">' + esc(code) + '</span>' + ord + '<br>' +
    esc(head) + (sub ? '<span class="meta">' + esc(sub) + '</span>' : '') +
    dimHtml(m) + '</span></label>';
}

function droppedHtml(d){
  if(!d.dropped || !d.dropped.length) return '';
  return '<details class="drop"><summary>отсеяно по твоим условиям: ' +
    d.dropped.length + '</summary>' + d.dropped.map(function(x){
      var m = D[x.loinc] || {};
      return '<div class="meta"><span class="code">' + esc(x.loinc) + '</span> ' +
        esc(m.ru || m.long) + ' — <b>' + esc(x.why) + '</b></div>';
    }).join('') + '</details>';
}

// Проверка моих решений. Контрол ровно из двух состояний: «верно» и «неверно».
// Третьего («не знаю») нет намеренно — оно эквивалентно ничего не отмечать, и
// отдельная кнопка для этого только создавала бы иллюзию вынесенного вердикта.
function auditHtml(d, i){
  var m = D[d.loinc] || {};
  var sp = SPEC[d.specimen] || d.specimen || '';
  return '<div class="item" id="a' + i + '"><div class="hd"><div>' +
    '<span class="nm">' + esc(d.name) + '</span> <span class="un">' + esc(d.unit) +
    '</span> <span class="tag">' + esc(sp) + '</span></div>' +
    '<div class="rows">' + d.rows + ' строк в каноне</div></div>' +
    '<div class="opts"><div style="padding:2px 0"><span class="code">' +
    esc(d.loinc) + '</span> ' + esc(m.ru || m.long) +
    (m.ru && m.long ? '<span class="meta">' + esc(m.long) + '</span>' : '') +
    dimHtml(m) + '<span class="meta">в наших строках: ' + esc(d.unit) +
    ((d.raw_units || []).length > 1 ? ' (написания: ' + esc(d.raw_units.join(', ')) + ')' : '') +
    '</span></div>' +
    '<div class="meta" style="padding:4px 0 8px">улика: ' + esc(d.rule || '—') + '</div>' +
    '<label class="opt" data-a="' + i + '" data-v="ok"><input type="radio" name="a' + i +
    '" value="ok"><span>верно, это оно</span></label>' +
    '<label class="opt" data-a="' + i + '" data-v="reject"><input type="radio" name="a' + i +
    '" value="reject"><span>неверно — сними, спрошу отдельно</span></label>' +
    '</div></div>';
}

function render(){
  var AUD = DATA.audit || [];
  if(AUD.length){
    document.getElementById('auditWrap').hidden = false;
    document.getElementById('audit').innerHTML =
      AUD.map(function(d, i){ return auditHtml(d, i); }).join('');
  }
  var ST = DATA.state || {};
  document.getElementById('chHd').textContent = ST.headline || 'Выбери код';
  if(ST.hint){
    var h = document.createElement('p');
    h.className = 'note';
    h.style.margin = '0 0 14px';
    h.textContent = ST.hint;
    document.getElementById('chHd').after(h);
  }
  if(ST.open_missing){ document.getElementById('missWrap').open = true; }
  document.getElementById('list').innerHTML = DATA.choices.map(function(d, i){
    var opts = d.options.map(function(c){ return optHtml(d, i, c); }).join('') +
      '<label class="opt" data-i="' + i + '" data-c="__none__">' +
      '<input type="radio" name="r' + i + '" value="__none__">' +
      '<span>ни один не подходит</span></label>';
    // Материал показан ЯВНО и не сливается с именем: «Калий, мг/л» в крови и в
    // моче — разные вопросы с разными ответами, и человек обязан видеть, какой
    // из них перед ним.
    var sp = SPEC[d.specimen] || d.specimen || '';
    return '<div class="item" id="i' + i + '"><div class="hd"><div>' +
      '<span class="nm">' + esc(d.name) + '</span> <span class="un">' + esc(d.unit) +
      '</span> <span class="tag">' + esc(sp) + '</span>' +
      '</div><div class="rows">' + d.rows + ' строк · ' + d.options.length +
      ' вариантов</div></div><div class="opts">' + opts + '</div>' +
      droppedHtml(d) + '</div>';
  }).join('');
  // Применённые факты названы вслух: отсев по факту, о котором человек не
  // помнит, что он его сообщил, — то же скрытое сужение, только вежливое.
  var f = DATA.facts || {};
  var applied = [];
  if(f.adult) applied.push('взрослый (материалы новорождённого отсеяны)');
  if(f.fasting_labs) applied.push('анализы натощак (нагрузочные пробы отсеяны)');
  var nDrop = 0;
  ['choices','auto','missing'].forEach(function(k){
    (DATA[k] || []).forEach(function(d){ nDrop += (d.dropped || []).length; }); });
  document.getElementById('sub').innerHTML = DATA.choices.length +
    ' пар требуют выбора · ' + DATA.auto.length + ' машина поставила сама · ' +
    DATA.missing.length + ' без кандидата' +
    (applied.length ? '<br><span class="meta">Учтены факты из профиля: ' +
      esc(applied.join('; ')) + '. Отсеяно кандидатов: ' + nDrop +
      ' — каждый виден внутри своей пары.</span>' : '');
  document.getElementById('autoSum').textContent =
    'Что машина поставила сама (' + DATA.auto.length + ') — тоже стоит просмотреть';
  document.getElementById('missSum').textContent =
    'Без кандидата (' + (DATA.missing || []).length + ') — имя не нашлось в LOINC';
  document.getElementById('doneSum').textContent =
    'Уже решено тобой (' + (DATA.decided || []).length + ') — второй раз не спрашиваем';
  document.getElementById('done').innerHTML = tbl(DATA.decided || [],
    ['имя','единица','материал','код','описание','строк'],
    function(d){ var m = D[d.loinc] || {};
      return '<td>' + esc(d.name) + '</td><td>' + esc((d.raw_units || []).join(' / ')) +
        '</td><td>' + esc(SPEC[d.specimen] || d.specimen || '') +
        '</td><td class="code">' + esc(d.loinc) + '</td><td>' +
        esc(m.ru || m.long) + '</td><td>' + d.rows + '</td>'; });
  document.getElementById('auto').innerHTML = tbl(DATA.auto, ['имя','единица','материал','код','описание','почему сама','строк'],
    function(d){
      var why = d.rule === 'generic-variant'
        ? 'кандидаты отличались только МЕТОДОМ (' + (d.over || []).length + '), ' +
          'взят вариант без метода; ключ тренда component у всех один: ' +
          (d.over || []).filter(function(c){ return c !== d.loinc; })
            .map(function(c){ return esc((D[c] || {}).long || c); }).join(' · ')
        : 'кандидат остался ровно один';
      var m = D[d.loinc] || {};
      return '<td>' + esc(d.name) + '</td><td>' + esc(d.unit) +
      '</td><td>' + esc(SPEC[d.specimen] || d.specimen || '') +
      '</td><td class="code">' + esc(d.loinc) + '</td><td>' +
      esc(m.ru || m.long) + '</td><td class="rows">' + why +
      '</td><td>' + d.rows + '</td>'; });
  document.getElementById('miss').innerHTML = tbl(DATA.missing, ['имя','единица','материал','строк'],
    function(d){ return '<td>' + esc(d.name) + '</td><td>' + esc(d.unit) +
      '</td><td>' + esc(SPEC[d.specimen] || d.specimen || '') +
      '</td><td>' + d.rows + '</td>'; });
  if(!DATA.choices.length && !(DATA.audit || []).length){
    document.getElementById('nextBtn').hidden = true;
    document.getElementById('exportBtn').hidden = true;
  }
  prog();
}
function tbl(rows, head, cell){
  return '<table><tr>' + head.map(function(h){ return '<th>' + h + '</th>'; }).join('') +
    '</tr>' + rows.map(function(d){ return '<tr>' + cell(d) + '</tr>'; }).join('') + '</table>';
}
function prog(){
  var a = Object.keys(verdicts).length, na = (DATA.audit || []).length;
  document.getElementById('prog').textContent =
    (na ? 'проверено ' + a + ' из ' + na + ' моих · ' : '') +
    'выбрано ' + Object.keys(picks).length + ' из ' + DATA.choices.length;
}
document.addEventListener('change', function(e){
  var lab = e.target.closest ? e.target.closest('label.opt') : null;
  if(!lab) return;
  if(lab.dataset.a !== undefined){
    var a = lab.dataset.a;
    verdicts[a] = lab.dataset.v;
    Array.prototype.forEach.call(
      document.querySelectorAll('label.opt[data-a="' + a + '"]'),
      function(l){ l.classList.toggle('on', l === lab); });
    document.getElementById('a' + a).classList.add('done');
    prog();
    return;
  }
  var i = lab.dataset.i;
  picks[i] = lab.dataset.c;
  Array.prototype.forEach.call(
    document.querySelectorAll('label.opt[data-i="' + i + '"]'),
    function(l){ l.classList.toggle('on', l === lab); });
  document.getElementById('i' + i).classList.add('done');
  prog();
});
document.getElementById('exportBtn').onclick = function(){
  var out = [];
  (DATA.audit || []).forEach(function(d, i){
    if(verdicts[i]) out.push({ name: d.name, unit: d.unit, specimen: d.specimen,
      loinc: d.loinc, verdict: verdicts[i] });
  });
  DATA.choices.forEach(function(d, i){
    if(picks[i]) out.push({ name: d.name, unit: d.unit, specimen: d.specimen,
      loinc: picks[i] === '__none__' ? null : picks[i] });
  });
  document.getElementById('out').value = JSON.stringify(out, null, 1);
  document.getElementById('exp').open = true;
  document.getElementById('out').select();
};
document.getElementById('nextBtn').onclick = function(){
  for(var a = 0; a < (DATA.audit || []).length; a++){
    if(!verdicts[a]){ document.getElementById('a' + a).scrollIntoView({behavior:'smooth'}); return; }
  }
  for(var i = 0; i < DATA.choices.length; i++){
    if(!picks[i]){ document.getElementById('i' + i).scrollIntoView({behavior:'smooth'}); return; }
  }
};
render();
</script>
</body></html>
"""


def _describe(conn, codes: list) -> dict:
    """Человеко-читаемое описание кандидатов: без него выбор невозможен."""
    if not codes:
        return {}
    q = ",".join("?" * len(codes))
    out = {}
    # Русское описание СОБИРАЕТСЯ из частей официального перевода LOINC, а не
    # переводится нами: длинного русского имени в релизе нет ни у одного кода
    # (замер 2026-07-29), а части переведены у 460 из 485 кодов листа. Где
    # перевода нет — остаётся английское имя, и это видно, а не замазано.
    for num, ln, sn, sysm, prop, units, ordered, rc, rs, rp, rm in conn.execute(
            f"SELECT t.loinc_num, t.long_common_name, t.shortname, t.system, "
            f"t.property, t.example_units, t.in_universal_order, "
            f"r.component, r.system, r.property, r.method "
            f"FROM loinc_terms t LEFT JOIN loinc_ru r USING (loinc_num) "
            f"WHERE t.loinc_num IN ({q})", codes):
        ru = " · ".join(x for x in (rc, rs, rp, rm) if x)
        out[num] = {"long": ln or "", "short": sn or "", "system": sysm or "",
                    "property": prop or "", "units": units or "",
                    "ordered": ordered, "ru": ru}
    return out


def render_worksheet(path: str) -> dict:
    """Лист выбора для человека: HTML, который открывается и размечается.

    Почему это выход ИМЕННО сопоставителя, а не отдельный модуль: машина довела
    работу до места, где нужен клинический вердикт, и обязана предъявить его в
    форме, пригодной для вынесения — с именем, материалом, свойством и единицами
    каждого кандидата. Таблица голых кодов была бы отчётом о собственной работе,
    а не инструментом решения.

    Состояние живёт в памяти страницы, решения выгружаются кнопкой в JSON.
    В браузерное хранилище НИЧЕГО не пишется намеренно: страница — черновик,
    а единственный дом вердикта — таблица lab_name_loinc с провенансом. Иначе
    появился бы второй дом решения, и через месяц было бы неясно, где правда.
    """
    import json as _json

    res = propose_mappings()
    with health_db.get_conn() as conn:
        health_db.attach_reference(conn)
        codes = sorted({c for d in res["choices"] for c in d["options"]}
                       | {d["loinc"] for d in res["auto"]}
                       | {c for d in res["auto"] for c in d.get("over", ())}
                       | {d["loinc"] for d in res["decided"]}
                       | {d["loinc"] for d in res["audit"]}
                       | {x["loinc"] for k in ("choices", "auto", "missing")
                          for d in res[k] for x in d.get("dropped", ())})
        res["desc"] = _describe(conn, codes)
    res["state"] = worksheet_state(res)
    payload = _json.dumps(res, ensure_ascii=False).replace("</", "<\\/")
    pathlib.Path(path).write_text(
        _WORKSHEET_HTML.replace("__PAYLOAD__", payload), encoding="utf-8")
    return {"choices": len(res["choices"]), "auto": len(res["auto"]),
            "missing": len(res["missing"]), "decided": len(res["decided"]),
            "audit": len(res["audit"]), "path": path}


def record_mapping(our_name: str, unit: str, loinc_num: str, decided_by: str,
           rule: str | None = None, specimen: str = "blood") -> None:
    """Записать соответствие с ПРОВЕНАНСОМ.

    `decided_by` обязателен и не имеет умолчания: через полгода отличить машинную
    догадку от решения человека можно будет только по этому полю, а в клинических
    данных это ровно та разница, которая имеет значение.
    """
    if not decided_by:
        raise ValueError("decided_by обязателен: соответствие без провенанса "
                         "неотличимо от догадки")
    # Единица нормализуется ЗДЕСЬ, а не у вызывающего: ключ отображения обязан
    # быть один. Иначе `pg/ml` и `пг/мл` завели бы две строки об одном и том же,
    # и следующий документ с третьим написанием не нашёл бы ни одну.
    with health_db.get_conn() as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO lab_name_loinc "
            "(our_name, unit, specimen, loinc_num, decided_by, rule) "
            "VALUES (?,?,?,?,?,?)",
            (our_name, lab_canon.norm_unit(unit), specimen, loinc_num,
             decided_by, rule))
        conn.commit()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-auto", action="store_true",
                    help="записать однозначные соответствия с провенансом machine")
    a = ap.parse_args()
    res = propose_mappings()
    print(json.dumps({k: len(v) for k, v in res.items()}, ensure_ascii=False))
    if a.apply_auto:
        for r in res["auto"]:
            record_mapping(r["name"], r["unit"], r["loinc"], "machine",
                           "cascade-2026-07-29", r.get("specimen", "blood"))
        print(f"записано автоматических: {len(res['auto'])}")
