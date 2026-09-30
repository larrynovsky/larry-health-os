#!/usr/bin/env python3.11
"""
lab_promote.py — промоут проверенного staging → канон lab_results.

Правило перезаписи (A1, решено 2026-06-30, инвентаризация источников):
  • ЗАМЕНИТЬ — все старые лаб-строки канона, КРОМЕ сохраняемых (ниже),
    удаляются; вместо них встают новые из staging.
  • НЕ ТРОГАТЬ (preserve) — не-лабораторное: `instrument:*` (опросники).
    (До 2026-09-23 здесь же стоял литерал источника-консультации владельца; строк с ним
    не осталось ни у одного тенанта и ни один писатель их не создаёт — ветка была мёртвой.)
  • Только кровь (panel ∈ BLOOD) идёт в lab_results; immunoreactivity/
    microbiome — НЕ туда.

Три гейта (Таненбаум: канон = Первичная копия, читателям нужна Строгая
согласованность):
  Гейт-1 diff old→new (что удаляется/встаёт/сохраняется, и что есть в
          старом, но НЕТ в новом — кандидаты на потерю);
  Гейт-2 инвариант полноты — старый (date, аналит) без покрытия в новом
          НЕ удаляется молча, а флагается;
  Гейт-3 атомарность (одна транзакция) + одиночный писатель (проверка).

БЕЗОПАСНОСТЬ: dry-run по умолчанию; --execute требует флага + pre-op снапшот.

  /opt/homebrew/bin/python3.11 lab_promote.py --run-id full2 [--reject-file r.json]
  /opt/homebrew/bin/python3.11 lab_promote.py --run-id full2 --execute
"""
from __future__ import annotations
from _time_inject import get_now  # seam
import argparse
import json
import logging
import shutil
import datetime as dt
from collections import defaultdict, Counter
from pathlib import Path

import health_db
import labs_db
import lab_canon

log = logging.getLogger(__name__)

# Константа BLOOD удалена 2026-08-01 (работа B). Она была объявлена здесь, ни разу
# не использована в модуле и успела разойтись с живой копией у второго писателя
# (`immunology` был только у неё). Граница домов теперь стережётся вердиктом
# человека в таблице `lab_domain_verdicts` + датчиком в ночном триаже.


def _is_preserved(source: str) -> bool:
    s = source or ""
    return s.startswith("instrument:")


def _canon(r, aliases: dict) -> str:
    # ПЕРВЫМ — подтверждение человека (F-14, вторая половина, закрыта 31.07).
    # До этого `aliases` приходил аргументом и не читался ни разу: глоссарий был
    # отключён СТРУКТУРНО, и 66 решений человека не влияли ни на одну строку.
    # Человек старше словаря в коде — на то он и оракул имени (§13).
    #
    # НО принимается только цель, которую канон знает. Замер 31.07: из 54 целей
    # глоссария 54 известны каноном кроме ОДНОЙ — `UricAcid` при каноническом
    # `Uric_acid`. Прими её на веру — и мочевая кислота разъедется на два тренда
    # молча, ровно тем способом, от которого весь этот слой и строился.
    # Неизвестная цель = провал на следующую ступень + WARN датчика
    # (`integrity_tests.check_glossary_targets_known`), а не тихое доверие.
    human = (aliases or {}).get((r["raw_name"] or "").strip().lower())
    if human and human in lab_canon.CANONICALS:
        return human
    # raw, если он сводится к ИЗВЕСТНОМУ канону (чинит Holotranscobalamin→B12,
    # коллизии Calcium); иначе модельный canonical (когда модель права: CEA);
    # иначе — очищенный raw. Не теряем.
    raw = lab_canon.normalize(r["raw_name"] or "")
    if raw in lab_canon.CANONICALS:
        return raw
    can = lab_canon.normalize(r["canonical_name"] or "")
    if can in lab_canon.CANONICALS:
        return can
    return raw or can or "?"


_SPECIMEN_BY_PANEL = {
    "cbc": "blood", "chemistry": "blood", "lipids": "blood", "hormones": "blood",
    "vitamins": "blood", "tumor_markers": "blood", "coagulation": "blood",
    "cardiac": "blood", "immunology": "blood", "markers": "blood",
    "immunoreactivity": "blood",
    # Варианты названия панели должны давать один материал.
    # Исправление распознавателя не переклассифицирует сохранённые строки.
    "oncomarkers": "blood", "онкомаркеры": "blood",
    "urine": "urine",
    "microbiome": "stool", "stool": "stool", "feces": "stool",
    "saliva": "saliva",
}


def canon_of(row) -> str:
    """Каноническое имя строки staging — ТАК ЖЕ, как его выведет промоут.

    Публичный вход по той же причине, что и `specimen_of`: имя нужно не только
    промоуту, но и сопоставлению с LOINC. И там оно обязано быть ТЕМ ЖЕ.

    `lab_results_staging.canonical_name` — снимок канонизатора на момент разбора.
    После изменения правил он может устареть. Все читатели должны выводить имя
    заново из raw_name тем же правилом, чтобы не возвращать разделённые склейки.
    """
    return _canon(row, {})


def specimen_of(row) -> str:
    """panel → материал (blood|urine|stool|saliva|other). Имя Urine_* → urine.

    Публичный вход: материал нужен не только промоуту, но и сопоставлению имён с
    LOINC — там он ЧАСТЬ КЛЮЧА. Без него `Калий, мг/л` из биохимии и из мочи
    (у мочи и референс в другом масштабе) — один ключ, и запись одного соответствия
    подшила бы мочу к сыворотке молча. Заводить второе такое правило рядом было
    бы вторым домом материала; правило остаётся здесь, вход становится публичным.
    """
    return _specimen(row)


# Сведение сырой строки бланка к классу материала ПЕРЕЕХАЛО в `lab_canon`
# 2026-08-01 (вердикт владельца 3): правило нужно обоим писателям канона, и пока
# оно жило у одного из них, второй импортировал первого. Здесь остаётся ссылка,
# а не копия — второй словарь рядом был бы вторым домом материала.
specimen_class = lab_canon.specimen_class


def _specimen(r) -> str:
    # Материал, прочитанный из бланка, приоритетнее вывода по панели.
    if (r.get("specimen_source") or "") in ("read_header", "read_footer", "continuation"):
        cls = specimen_class(r.get("specimen"))
        if cls:
            return cls
    p = (r.get("panel") or "").strip().lower()
    if p in _SPECIMEN_BY_PANEL:
        return _SPECIMEN_BY_PANEL[p]
    if (r.get("canonical_name") or "").startswith("Urine_"):
        return "urine"
    return "other"


# Валютные единицы = цена из инвойса, не лаб-результат (#67).
_CURRENCY_UNITS = {"€", "eur", "nis", "₪", "$", "usd", "gbp", "£", "руб", "rub", "shekel"}


def _block_reason(r) -> str | None:
    """Layer-2 гейт целиком: МАРШРУТНАЯ причина, затем универсальное правило результата.

    Порядок выбран так, а не «результат первым» (2026-08-08, поймано пятью красными
    тестами биллинг-гейта). Функция возвращает ОДНУ причину, и она едет человеку как
    объяснение. Для строки из инвойса в евро правильный ответ — «currency-unit», а не
    «нет результата»: первый называет корень, второй — следствие. Универсальные
    правила поэтому применяются ПОСЛЕДНИМИ — к строке, которую все маршрутные правила
    уже согласились впустить.

    Проверка результата должна охватывать и разрешающие ветки маршрутизации:
    разрешённый маршрут сам по себе не гарантирует наличия значения.
    """
    routed = _block_reason_route(r)
    if routed:
        return routed
    # РЕЗУЛЬТАТ ОБЯЗАН БЫТЬ. Правило одно и живёт в `lab_canon.result_violation`.
    _rv = lab_canon.result_violation(r.get("value"), r.get("value_text"))
    if _rv:
        return f"no-result:{_rv}"
    # СЛОВАРЬ КАЧЕСТВЕННЫХ. Незнакомое слово не едет в канон — оно едет человеку.
    # Не строгость ради строгости: значение попадает в промпт LLM (gp_context), и
    # свободный текст из документа открыл бы канал «содержимое бланка → инструкция
    # модели» (§19). Сырое слово НЕ теряется: оно остаётся в staging.
    if (r.get("value_text") or "").strip() and not r.get("_vtext"):
        # Путь к справке — в ТЕКСТЕ блока, а не «где-то в доках»: эту строку читает
        # человек в очереди ревью, и решение «пополнить словарь или отклонить» он
        # принимает здесь. Ссылка ради ссылки бесполезна, вход читателя — нет.
        return (f"unknown-qualitative:{str(r.get('value_text'))[:40]} "
                f"(словарь и правило: docs/reference/qualitative_lab_values.md)")
    return None


def _block_reason_route(r) -> str | None:
    """Маршрутные причины: не-лаб мусор, чужой дом, неизвестный аналит. #67.

    Требует, чтобы к моменту вызова были проставлены r['_specimen'], r['_cname'],
    r['_class'] и r['_home'] — всё это делает `prepare`. Отсутствие `_home`
    означает «строку никто не готовил», и доменный гейт ниже её ЗАБЛОКИРУЕТ:
    вызвать этот гейт мимо подготовки нельзя, и молчать об этом он не станет.
    """
    # Страница-график или проза не является таблицей измерений.
    # Производные величины и фрагменты комментариев не должны попадать
    # в клинический канон как самостоятельные результаты или имена.
    # NULL = страницу никто не смотрел (документ не PDF или разбор до 31.07) —
    # такие строки не блокируем, иначе новое правило задним числом выкосило бы
    # историю, о которой оно ничего не знает.
    if (r.get("page_role") or "data") != "data":
        return f"page-role:{r.get('page_role')}"
    # Дом класса определяет человек в lab_domain_verdicts.
    # Каждый писатель обязан исполнять эту границу, чтобы не создавать два дома.
    # None (класса нет в вердиктах) блокирует ТОЖЕ: новый класс не имеет права
    # въехать в клинический канон по умолчанию. Блок = строка живёт в staging
    # на ревью, а не удаляется (§13, ступень 2 — fail-closed).
    if r.get("_home") != "canon":
        return f"domain:{r.get('_class')}→{r.get('_home') or 'БЕЗ ВЕРДИКТА'}"
    if (r.get("unit") or "").strip().lower() in _CURRENCY_UNITS:
        return "currency-unit"
    # Для перечисленных материалов доверяем canonical_name модели.
    # Уточнение материала не должно само по себе менять маршрут строки.
    if r.get("_specimen") in ("urine", "stool", "saliva", "throat_swab"):
        return None
    if (r.get("canonical_name") or "").startswith("Urine_"):
        return None
    # кровь/other: имя обязано сводиться к ИЗВЕСТНОМУ аналиту, иначе это
    # процедура/услуга/лекарство (операция/PET-CT/торговое имя препарата) или требует
    # маппинга (тогда — сначала алиас в ревью, потом промоут).
    _cn = lab_canon.normalize(r.get("_cname") or "")
    if _cn in lab_canon.CANONICALS:
        # известный аналит — но НЕ пускаем физически невозможное значение:
        # инвойс-количество Amylase=2.0/Iron=1.0 = имя реальное, значение мусор.
        v = r.get("value")
        if v is not None:
            import lab_oracles
            vv, _u = lab_canon.to_conventional(_cn, v, r.get("unit"))
            try:
                if _cn in lab_oracles._HARD and not (lab_oracles._HARD[_cn][0] <= vv <= lab_oracles._HARD[_cn][1]):
                    return "impossible-value"
                if _cn in lab_oracles._PCT and not (0 <= vv <= 100):
                    return "impossible-pct"
            except (TypeError, ValueError):
                pass
        return None
    return "unmapped-nonanalyte"


def _staler_than_promoted(run_id: str, sources: set) -> dict:
    """{источник: run_id, промоутнутый позже} — пусто, если этот прогон свежайший.

    Порядок берётся из `created_at` строк staging, а НЕ из имени run_id: имена
    лексикографически не упорядочены («qual20260808» < «…b» — совпадение, а не
    правило), и опереться на них значило бы построить гарантию на удаче.

    Промоутнутость читается из `review_status='promoted'`, который промоут ставит
    САМ по факту вставки (правило 2026-07-31), — то есть из следа события, а не из
    догадки о нём.
    """
    if not sources:
        return {}
    out = {}
    with health_db.get_conn() as conn:
        mine = dict(conn.execute(
            "SELECT source_file, MAX(created_at) FROM lab_results_staging "
            "WHERE run_id=? GROUP BY source_file", (run_id,)).fetchall())
        rows = conn.execute(
            "SELECT source_file, run_id, MAX(created_at) FROM lab_results_staging "
            "WHERE review_status='promoted' AND run_id<>? GROUP BY source_file, run_id",
            (run_id,)).fetchall()
    newest = {}
    for src, rid, ts in rows:
        if src in sources and (src not in newest or ts > newest[src][1]):
            newest[src] = (rid, ts)
    for src, (rid, ts) in newest.items():
        if mine.get(src) and mine[src] < ts:
            out[src] = rid
    return out


def _apply_rejects(conn, run_id, reject_file):
    if not reject_file:
        return 0
    rej = json.load(open(reject_file))
    n = 0
    for e in rej:
        sf, name = e.get("source_file"), e.get("canonical")
        val = e.get("value")
        try:
            fval = float(val)
        except (TypeError, ValueError):
            fval = None
        cur = conn.execute(
            "UPDATE lab_results_staging SET review_status='rejected', "
            "status_changed_at=datetime('now') "
            "WHERE run_id=? AND source_file=? AND (canonical_name=? OR raw_name=?) "
            "AND ABS(COALESCE(value,-1e30) - ?) < 1e-6",
            (run_id, sf, name, name, fval if fval is not None else -1e30))
        n += cur.rowcount
    conn.commit()
    return n


# Резерв порога «два числа есть одно измерение». НЕ альтернативная норма, а ЗЕРКАЛО
# сида `system_config['lab.conflict_spread']` (health_db.init_db) — равенство держит
# coherence-тест. Читать надо `conflict_spread()`, а не эту константу: она существует
# только для случая, когда БД недоступна, и молча разойтись с сидом не имеет права.
_SPREAD_FALLBACK = 0.01

# Ступени лестницы `lab_specimen.method_facts`, при которых метод ПРОЧИТАН из
# бланка. Всё остальное — не метод, а его отсутствие, и в ключ не идёт.
_METHOD_READ_STEPS = ("read_name", "read_section", "read_study")


def conflict_spread(conn=None) -> float:
    """Порог расхождения — из данных (§9, методическая константа, оракул инженер).

    Ручка не косметическая: она решает, сколько измерений доезжает до канона.
    Пример: значение магния в ммоль/л и в мг/л (одно вещество, две шкалы) после
    приведения может расходиться чуть больше чем на 1 % — при пороге 1 % это
    конфликт, при 1,1 % они сходятся.
    Такое решение принимает человек, поэтому значение живёт в БД, а не здесь.

    `conn` передаёт вызывающий по той же причине, что и словарь имён: датчик ходит
    по тенантам read-only, и порог обязан читаться из БД того же тенанта.
    """
    try:
        import config_db
        return float(config_db.get_config("lab.conflict_spread", _SPREAD_FALLBACK,
                                          conn=conn))
    except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО
        log.warning("lab.conflict_spread недоступен в БД, взят резерв %s "
                    "(§14: тихий fallback запрещён)", _SPREAD_FALLBACK)
        return _SPREAD_FALLBACK


def prepare(rows: list, aliases: dict) -> tuple[list, list]:
    """staging-строки → (kept, blocked): проставлены `_specimen`/`_cname`, Layer-2 отсеян.

    Вынесено из `plan()` 2026-07-31, чтобы у подготовки строки был ОДИН дом: её
    зовёт и промоут (решает, что писать в канон), и ночной датчик (решает, о чём
    краснеть). Второй такой цикл рядом развёл бы канон с монитором молча — ровно
    тот класс расхождения, который эта нить лечила у имени аналита.

    Строки мутируются на месте (как и до выноса): вызывающий получает те же dict-ы.
    """
    # Вердикт «класс → дом» читается ОДИН раз на подготовку, а не построчно:
    # это решение человека, оно не меняется в середине прогона.
    verdicts = labs_db.domain_verdicts()
    for r in rows:
        r["_class"] = lab_canon.classify_row(r.get("raw_name"), r.get("panel"))
        r["_home"] = verdicts.get(r["_class"])
        r["_specimen"] = _specimen(r)
        # имя аналита: для НЕ-крови берём канон модели (Urine_* и т.п. — модель
        # выбрала его по панели). Панель-слепой normalize тут нельзя: турецкие
        # GLUKOZ/PROTEİN/BİLİRUBİN мочи столкнулись бы с кровяными. Для крови —
        # прежний raw-preferred _canon (чинит Holotranscobalamin→B12 и т.п.).
        if r["_specimen"] != "blood" and (r.get("canonical_name") or "").strip():
            r["_cname"] = r["canonical_name"].strip()
        else:
            r["_cname"] = _canon(r, aliases)
        # Размерность уточняет имя ЗДЕСЬ, а не в канонизаторе: `normalize` видит
        # только строку, а различие «10^9/л против %» живёт в единице.
        # Материал так уточнять НЕЛЬЗЯ — у него есть своя колонка, и суффикс в
        # имени завёл бы ему второй дом (см. lab_canon._DIMENSION_BY_UNIT).
        r["_cname"] = lab_canon.dimension_key(r["_cname"], r.get("unit") or "")
        # МЕТОД в ключ (2026-07-31, шаг 6). Берётся ТОЛЬКО вместе со ступенью
        # чтения: метод без провенанса неотличим от назначенного, а вся нить
        # началась с того, что материал был назначен, а не измерен.
        r["_method"] = (r.get("method")
                        if (r.get("method_source") or "") in _METHOD_READ_STEPS
                        else None)
        # Качественный результат сводится к словарю ЗДЕСЬ — там же, где сырое имя
        # сводится к каноническому. Симметрия не косметическая: у распознавателя
        # одна работа (прочитать, что напечатано), у промоута другая (свести к
        # канону), и смешивать их значит потерять сырое написание.
        r["_vtext"] = lab_canon.normalize_value(r.get("value_text"))
        # Микроскопия осадка (2026-08-31): предложение «N-M LÖKOSİT VE NADİR
        # ERİTROSİT GÖRÜLDÜ» → число с оператором для СВОЕЙ клетки либо токен.
        # Только когда числа нет и словарь слово не узнал — иначе не трогаем.
        if r.get("value") is None and (r.get("value_text") or "").strip() and not r["_vtext"]:
            mc = lab_canon.microscopy_count(r.get("value_text"), r["_cname"])
            if mc and "value" in mc:
                r["value"], r["value_op"] = mc["value"], mc["value_op"]
                r["unit"] = r.get("unit") or mc["unit"]
                r["value_text"] = None       # число вместо прозы; сырое остаётся в staging
            elif mc:
                r["_vtext"] = mc["token"]

    # Layer-2 гейт (#67, 2026-07-02): не пускаем в промоут не-лаб мусор из инвойсов.
    # Блок: (1) валютная единица (цена, не результат); (2) кровяной аналит, не
    # сводящийся к известному канону (процедуры/услуги/лекарства типа
    # операция/торговое имя препарата). Не-кровь (urine/stool/saliva, Urine_*) доверяем модели.
    # Блок = НЕ промоутить (строка живёт в staging на ревью), НЕ удаление.
    blocked, kept = [], []
    for r in rows:
        reason = _block_reason(r)
        (blocked.append((r, reason)) if reason else kept.append(r))
    return kept, blocked


def split_groups(kept: list, spread: float | None = None) -> tuple[list, list]:
    """Подготовленные строки → (promote, conflicts) по ключу (дата, имя, материал).

    Единое правило сравнения измерений: группа с расхождением больше порога
    не промоутится целиком. Выбор победителя по порядку строк терял бы данные.

    Сравнение идёт в общей шкале, иначе различие единиц станет ложным конфликтом.
    Приведение — то же самое `to_conventional`, которым строка попадает в канон;
    второго правила шкалы здесь нет и быть не должно.

    Конфликт — НАХОДКА, а не деталь отчёта: его читает
    `integrity_tests.check_promote_conflicts`. Поэтому возвращается структура с
    страницами и сырыми именами, а не строка для печати: разобрать конфликт можно
    только зная, откуда в бланке пришло каждое значение.
    """
    if spread is None:
        spread = conflict_spread()
    groups = defaultdict(list)
    for r in kept:
        groups[(r["date"], r["_cname"], r["_specimen"])].append(r)

    promote, conflicts = [], []
    for key, g in sorted(groups.items()):
        # Метод разводит только расходящиеся значения. Безусловный ключ по методу
        # расщепил бы повторную печать одного измерения в разные ряды.
        # Граница правила: совпавшие значения объединятся, а расходящиеся
        # могут разделиться по методам; число точек зависит от данных даты.
        for sub in _split_by_method_if_needed(g, key[1], spread):
            vals, raws = [], []
            for x in sub:
                if x["value"] is None:
                    continue
                v, _u = lab_canon.to_conventional(key[1], x["value"], x.get("unit"))
                vals.append(v)
                raws.append(f"{x['value']} {x.get('unit') or ''}".strip())
            if vals and (max(vals) - min(vals)) / max(abs(max(vals)), abs(min(vals)),
                                                      1e-9) > spread:
                conflicts.append({
                    "date": key[0], "canonical": key[1], "specimen": key[2],
                    "method": sub[0].get("_method"),
                    "values": sorted(set(vals)),
                    "raw_values": sorted(set(raws)),
                    "pages": sorted({x["page"] for x in sub if x.get("page") is not None}),
                    "raw_names": sorted({x["raw_name"] for x in sub if x.get("raw_name")}),
                })
                continue
            # КАЧЕСТВЕННОЕ РАСХОЖДЕНИЕ (2026-08-08). Числовой конфликт ловится
            # радиусом выше, но строки без числа проходят мимо него (`value is None`
            # → continue), и группа, где одно зрение прочло «отрицательно», а другое
            # «обнаружено», молча выбрала бы победителя по порядку id. Для
            # качественного результата разница между токенами — не шум радиуса, а
            # противоположный ответ; выбирать его сортировкой нельзя.
            _tokens = {x.get("_vtext") for x in sub if x.get("_vtext")}
            if len(_tokens) > 1:
                conflicts.append({
                    "date": key[0], "canonical": key[1], "specimen": key[2],
                    "method": sub[0].get("_method"),
                    "values": sorted(_tokens),
                    "raw_values": sorted({(x.get("value_text") or "").strip()
                                          for x in sub if (x.get("value_text") or "").strip()}),
                    "pages": sorted({x["page"] for x in sub if x.get("page") is not None}),
                    "raw_names": sorted({x["raw_name"] for x in sub if x.get("raw_name")}),
                })
                continue
            best = sorted(sub, key=lambda x: (x["value_agreement"] != "agree", x["id"]))[0]
            best["_canon"] = key[1]
            best["_specimen"] = key[2]
            promote.append(best)
    return promote, conflicts


def _split_by_method_if_needed(g: list, canon: str, spread: float) -> list[list]:
    """Группа → одна подгруппа, если значения согласны; иначе — по методам.

    Возврат ровно одной подгруппы — нормальный случай, а не вырожденный: он
    сохраняет поведение, которое было до появления метода, для всех строк, где
    приборы сошлись.
    """
    vals = [lab_canon.to_conventional(canon, x["value"], x.get("unit"))[0]
            for x in g if x["value"] is not None]
    if not vals or (max(vals) - min(vals)) / max(abs(max(vals)), abs(min(vals)),
                                                 1e-9) <= spread:
        return [g]
    by_method = defaultdict(list)
    for x in g:
        by_method[x.get("_method")].append(x)
    if len(by_method) < 2:
        return [g]   # метода нет или он один — расхождение остаётся конфликтом
    return [by_method[k] for k in sorted(by_method, key=lambda z: z or "")]


def pending_conflicts(conn, aliases: dict) -> list:
    """Группы, которые промоут ОТКАЗЫВАЕТСЯ сливать, среди всего, что ещё ждёт канона.

    Предикат шире, чем у `plan()`: не один прогон, а всё неотвергнутое и ещё не
    промоутнутое. Так спрашивает датчик — его вопрос «какие измерения система прямо
    сейчас отказывается класть в канон», и он не знает про run_id.

    Повторный разбор одной страницы не должен создавать фантомный конфликт
    между строкой и её копией из другого прогона.

    Соединение и словарь имён передаёт ВЫЗЫВАЮЩИЙ: датчик целостности ходит по
    тенантам read-only и обязан читать имена из БД ТОГО ЖЕ тенанта, иначе алиасы
    владельца применятся к строкам партнёра.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM lab_results_staging "
        "WHERE COALESCE(review_status,'pending') NOT IN ('rejected','promoted')")]
    kept, _blocked = prepare(rows, aliases)
    _promote, conflicts = split_groups(kept, spread=conflict_spread(conn))
    return conflicts


def plan(run_id: str, reject_file: str | None, execute: bool):
    health_db.init_db()
    try:
        # F-14 закрыт 31.07: было `get_confirmed_aliases(0)` — формата 0 не
        # существует, поэтому 66 подтверждений человека не влияли ни на одну
        # строку. У строки staging формата НЕТ, поэтому глоссарий читается
        # целиком; конфликты между форматами словарь выбрасывает сам.
        aliases = labs_db.get_confirmed_aliases_all() or {}
    except Exception:
        aliases = {}

    with health_db.get_conn() as conn:
        rej_n = _apply_rejects(conn, run_id, reject_file)
        # COALESCE, а не `review_status!='rejected'`: в SQLite NULL != 'rejected' даёт
        # NULL, и строка выпадала из промоута БЕЗ СЛЕДА — неотличимо от «её не было».
        # Умолчание берётся из схемы (`DEFAULT 'pending'`), а не выдумывается здесь.
        # Оракул: tests/unit/test_lab_promote.py::test_null_review_status_promotes_like_pending
        new_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM lab_results_staging "
            "WHERE run_id=? AND COALESCE(review_status,'pending')!='rejected'",
            (run_id,))]
        old_rows = [dict(r) for r in conn.execute(
            "SELECT id, date, test_name, value, source, specimen FROM lab_results")]

    # --- категории старого канона ---
    preserve = [r for r in old_rows if _is_preserved(r["source"])]
    to_delete = [r for r in old_rows if not _is_preserved(r["source"])]

    new_rows, blocked = prepare(new_rows, aliases)
    promote, conflicts = split_groups(new_rows)

    # Частичные записи должны применяться монотонно: старый прогон поверх нового
    # может восстановить уже исправленное имя или результат.
    #
    # Частичные записи требуют монотонности: применять их вразнобой нельзя. Отказ,
    # а не предупреждение: предупреждение перед разрушающей записью читают уже после.
    stale = _staler_than_promoted(run_id, {r["source_file"] for r in promote})
    if stale:
        raise ValueError(
            "прогон СТАРЕЕ уже промоутнутого по тому же источнику — откат свежего "
            "знания: " + "; ".join(f"{s}: промоутнут {w}" for s, w in sorted(stale.items())) +
            f". Перечитай источник заново, а не промоуть {run_id}.")

    # --- Гейт-2: полнота. старый (date, аналит, материал) без покрытия в новом ---
    # МАТЕРИАЛ В КЛЮЧЕ ПОКРЫТИЯ (2026-08-08). До этой правки ключ группировки
    # (`split_groups`: date+имя+материал) и ключ ПОКРЫТИЯ (здесь: date+имя) РАЗЪЕХАЛИСЬ
    # внутри одного модуля — и удаление оказалось шире замены.
    #
    # Найдено замером на живом прогоне: под одной датой и каноном `WBC` в каноне лежат
    # ДВЕ строки — кровь (×10^9/л, «Лейкоцитарные параметры») и моча (кл/мкл,
    # «Количественное исследование осадка мочи»). Прогон одной страницы принёс
    # только мочевую. Слепой к материалу ключ пометил бы покрытыми ОБЕ, удалил обе и
    # вставил одну: анализ крови исчез бы из канона МОЛЧА.
    #
    # Оракула у этого класса потери не было ПО ПОСТРОЕНИЮ: Гейт-2 считает потерянным
    # только НЕПОКРЫТОЕ, а здесь ключ как раз покрыт — гейт обязан был молчать.
    # Класс: [[feedback_one_question_one_key]] — ключ, по которому СПРАШИВАЮТ,
    # разъехался с ключом, по которому ЛЕЖИТ.
    #
    # Уточнение ключа материалом защищает чужой образец от удаления.
    # Отсутствующий материал требует отдельного разбора, а не догадки по имени.
    new_keys = {(r["date"], r["_canon"], r["_specimen"]) for r in promote}

    # --- Гейт-2b: ПРИТОК. Зеркало гейта полноты, заведено 2026-07-31.
    #
    # «Потеряно 0» и «не пришло лишнего» — РАЗНЫЕ утверждения, и второе из первого
    # не следует. Гейт полноты защищает от удаления и по построению не спрашивает,
    # хорошо ли добавленное; когда причина мусора была найдена и промоут повторён,
    # он ЭТОТ ЖЕ мусор и сохранил — как «старое непокрытое».
    #
    # Проверка только потерь не обнаружит приток производных графиков или прозы.
    # Зеркальный предикат показывает новые строки и новые имена.
    #
    # Печатается, а не блокирует: порог «сколько нового — слишком много» зависит от
    # документа (первый бланк новой лаборатории законно приносит десятки имён), и
    # выдумывать его наперёд значило бы получить либо шум, либо ложный комфорт.
    # Предмет здесь — ВИДИМОСТЬ притока перед разрушающей записью, а не запрет.
    old_keys = {(r["date"], lab_canon.normalize(r["test_name"]), r["specimen"])
                for r in old_rows}
    old_names = {lab_canon.normalize(r["test_name"]) for r in old_rows}
    arrived = sorted(new_keys - old_keys)
    fresh_names = sorted({n for _d, n, _s in arrived if n not in old_names})

    lost = []
    lost_ids = set()
    for r in to_delete:
        on = lab_canon.normalize(r["test_name"])
        # ЗАЩИТА ОТ ПОТЕРИ: сохраняем старое (date, аналит), которого нет в новом
        # прогоне. Условие `on not in new_names` УБРАНО (2026-07-01): оно стирало
        # историю аналита по ДРУГИМ датам, если прогон содержал его хоть на одной
        # дате → инкрементный промоут одного бланка выкашивал весь лонгитюд. Теперь
        # промоут заменяет ТОЛЬКО покрытые (date, аналит), остальные даты живут.
        if (r["date"], on, r["specimen"]) not in new_keys:
            lost.append((r["date"], r["test_name"], r["value"], r["source"]))
            lost_ids.add(r["id"])

    # --- отчёт (Гейт-1 diff) ---
    print(f"=== ПРОМОУТ {run_id} ({'EXECUTE' if execute else 'DRY-RUN'}) ===")
    print(f"reject помечено: {rej_n}")
    print(f"\nСТАРЫЙ канон: {len(old_rows)} строк")
    print(f"  сохранить (instrument:*): {len(preserve)}")
    print(f"  удалить (лаб, заменяется): {len(to_delete)}")
    print(f"\nНОВОЕ (staging {run_id}):")
    print(f"  к промоуту после дедупа: {len(promote)}  (конфликтов на ревью: {len(conflicts)})")
    print(f"  по материалам: {dict(Counter(r['_specimen'] for r in promote))}")
    if blocked:
        by_reason = Counter(reason for _, reason in blocked)
        print(f"\nLayer-2 ЗАБЛОКИРОВАНО (не промоут, остаётся в staging на ревью): "
              f"{len(blocked)} — {dict(by_reason)}")
        for r, reason in blocked[:15]:
            print(f"    [{reason}] {r.get('date')} {str(r.get('_cname'))[:26]} = "
                  f"{r.get('value')} {r.get('unit') or ''}")
        if len(blocked) > 15:
            print(f"    … ещё {len(blocked) - 15}")
    print(f"\nГейт-2 ПОЛНОТА: старых (date, аналит) без покрытия в новом: {len(lost)} "
          f"— они НЕ удаляются (старое значение сохраняется, потеря исключена):")
    for d, n, v, s in lost[:25]:
        print(f"    {d} {str(n)[:28]} = {v}  [{str(s)[:22]}]")
    if len(lost) > 25:
        print(f"    … ещё {len(lost) - 25}")
    print(f"\nГейт-2b ПРИТОК: новых (date, аналит) — {len(arrived)}; "
          f"имён, которых в каноне НЕ БЫЛО НИКОГДА — {len(fresh_names)}:")
    for n in fresh_names[:25]:
        print(f"    + {n}")
    if len(fresh_names) > 25:
        print(f"    … ещё {len(fresh_names) - 25}")
    if conflicts:
        print(f"\nКонфликты значений (на ревью, не промоутятся):")
        for c in conflicts[:15]:
            print(f"    {c['date']} {str(c['canonical'])[:28]}: {c['values']} "
                  f"стр={c['pages']}")

    total_after = len(preserve) + len(lost) + len(promote)
    print(f"\nИТОГ канона после промоута: {len(preserve)} (не-лаб) + {len(lost)} (старое непокрытое) "
          f"+ {len(promote)} (новое) = {total_after}")

    if not execute:
        print("\nDRY-RUN — канон не тронут. Записать: --execute (снапшот + транзакция).")
        return {"preserve": len(preserve), "delete": len(to_delete) - len(lost_ids),
                "promote": len(promote), "lost": len(lost), "conflicts": len(conflicts),
                "blocked": len(blocked),
                # Зеркало `lost`. Возвращается, а не только печатается: предикат,
                # который нельзя прочитать программно, проверяется только глазами,
                # а глаза — тот самый оракул, который сегодня и промахнулся.
                "arrived": len(arrived), "fresh_names": fresh_names}

    # --- Гейт-3: атомарная замена. Удаляем ТОЛЬКО покрытое новым;
    #     непокрытое старое сохраняем (потеря исключена по построению). ---
    dbp = Path(health_db.DB_PATH)
    snap = dbp.with_suffix(f".presnap_{get_now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(dbp, snap)
    print(f"\nснапшот: {snap}")
    cols = ("date", "source", "test_name", "value", "value_text", "unit",
            "ref_low", "ref_high", "status", "specimen", "value_op", "method")
    del_ids = [r["id"] for r in to_delete if r["id"] not in lost_ids]
    with health_db.get_conn() as conn:
        conn.execute("BEGIN")
        conn.executemany("DELETE FROM lab_results WHERE id=?", [(i,) for i in del_ids])
        ins = []
        for r in promote:
            val, unit = lab_canon.to_conventional(r["_canon"], r["value"], r["unit"])
            # Значение и референсы конвертируются одним правилом одновременно.
            # Смешение шкал может создать ложную тревогу в downstream-читателях.
            lo, hi = lab_canon.to_conventional_range(
                r["_canon"], r["ref_low"], r["ref_high"], r["unit"])
            # Оператор едет ВМЕСТЕ со значением. Отдельно они бессмысленны: число
            # без оператора читается как измерение, оператор без числа — ничто.
            # В канон едет ТОКЕН словаря (`_vtext`), а не сырое слово из документа.
            # Сырое остаётся в staging: канон — контролируемое множество из шести
            # значений, и это же делает невозможным канал «текст бланка → промпт».
            ins.append((r["date"], f"doc:{r['source_file']}", r["_canon"], val,
                        r.get("_vtext"), unit, lo, hi, r["doc_flag"], r["_specimen"],
                        r.get("value_op"), r.get("_method")))
        conn.executemany(
            f"INSERT INTO lab_results ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", ins)
        # Статус ставится по факту вставки в той же транзакции.
        # Сопоставление имён постфактум не доказывает, что строка принята.
        conn.executemany("UPDATE lab_results_staging SET review_status='promoted', "
                         "status_changed_at=datetime('now') "
                         "WHERE id=?", [(r["id"],) for r in promote])
        conn.commit()
    print(f"ЗАМЕНЕНО: удалено {len(del_ids)} (покрытое), вставлено {len(promote)}. "
          f"Сохранено: {len(preserve)} не-лаб + {len(lost)} старых непокрытых.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--reject-file", default=None)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    plan(a.run_id, a.reject_file, a.execute)
