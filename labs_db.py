"""labs_db.py — доменный модуль labs. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import i18n
import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)

# Незавершённые строки: auto — согласие моделей, а не подтверждение человека.
# specialized уже приняты отдельным писателем; неизвестный статус остаётся видимым.
WAITING_REVIEW_SQL = "COALESCE(review_status,'pending') NOT IN ('rejected','promoted','specialized')"


def get_recent_labs(n_days: int = 365, key_tests: list = None, exclude_pro: bool = True) -> list[dict]:
    """
    Возвращает последний результат для каждого теста за последние n_days дней.
    Если key_tests задан — только эти тесты.

    exclude_pro=True (default): исключает записи с source LIKE 'instrument:%'
    (это PRO-подшкалы опросников — survivorship-extension).
    Передать exclude_pro=False, чтобы получить ВСЕ записи включая PRO.
    """
    _hdb._ensure_lab_table()
    cutoff = str(get_today() - timedelta(days=n_days))
    pro_filter = " AND (source IS NULL OR source NOT LIKE 'instrument:%')" if exclude_pro else ""
    with _hdb.get_conn() as conn:
        if key_tests:
            placeholders = ",".join("?" * len(key_tests))
            rows = conn.execute(f"""
                SELECT test_name, value, value_text, unit, status, date,
                       ref_low, ref_high, source, MAX(date) as last_date
                FROM lab_results
                WHERE date >= ? AND test_name IN ({placeholders}){pro_filter}
                GROUP BY test_name
                ORDER BY test_name
            """, [cutoff] + key_tests).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT test_name, value, value_text, unit, status, date,
                       ref_low, ref_high, source, MAX(date) as last_date
                FROM lab_results
                WHERE date >= ?{pro_filter}
                GROUP BY test_name
                ORDER BY test_name
            """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


# Окно промпт-среза: сколько дней лабораторной истории едет в контекст LLM. ОДИН дом на
# все контексты (gp_context, wellally_consult) и на сторожа, который судит формулировку
# «нет данных за окно». Разъехавшись, срез и судья снова заспорили бы о том, что есть в
# реальности — ровно этот спор и отклонил отчёт 13.09. Не клинический порог (§9), а
# бюджет показа: сколько строк врач в состоянии прочитать.
PROMPT_WINDOW_DAYS = 730


def canon_window_note(n_days: int, exclude_pro: bool = True, shown_tests: list = None,
                      named_below: bool = False) -> str:
    """Объявление границы окна лаб-блока: что показано и что лежит ЗА границей.

    `shown_tests` — список имён, если блок фильтрует аналиты не только окном
    (например, через `patient_context._LABS_SNAPSHOT_TESTS`);
    передай сюда этот список: объявление тогда называет ОБА фильтра. Без него
    объявлялось бы только окно, а невидимость по списку оставалась бы необъявленной —
    то есть та же «я этого не вижу» → «этого не было», от которой стоит весь механизм.

    Окно скрывает старые строки, но не доказывает, что измерений не было.
    Изнутри промпта отсутствие данных и выход за окно неразличимы,
    поэтому граница объявляется в самой копии.

    Сырые имена за окном сюда не передаются: текст документа не должен
    становиться инструкцией модели (§19). Даты и счёт показывают наличие
    истории без переноса произвольных фрагментов бланка.

    `named_below=True` — если ниже в том же блоке вызывается
    `build_unrepeated_draw_context`, который перечисляет часть имён за окном.
    Флаг согласует объявление границы с содержимым соседнего блока.
    Он задаётся явно, а не автоопределением: объявление не должно знать
    внутренности соседнего блока, зато вызывающий знает, зовёт он его или нет. Из
    четырёх читателей (`gp_context`, `patient_context`, `wellally_consult`,
    `hai_context`) блок зовёт один, и остальным текст менять нельзя — для них
    безоговорочная формулировка верна.
    """
    _hdb._ensure_lab_table()
    # n_days=None — у блока НЕТ окна, он отбирает только именами (21.09: так устроен
    # build_lab_history_context — min_points и «только числовые»). Граница тогда одна,
    # и объявляется она той же функцией: второй дом текста разъехался бы с первым.
    cutoff = str(get_today() - timedelta(days=n_days)) if n_days is not None else ""
    pro_filter = " AND (source IS NULL OR source NOT LIKE 'instrument:%')" if exclude_pro else ""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT test_name, MAX(date) AS last_date FROM lab_results "
            f"WHERE date IS NOT NULL{pro_filter} GROUP BY test_name"
        ).fetchall()
    if not rows:
        return ""          # канон пуст — объявлять нечего, а «значит не сдавался» было бы шумом
    shown  = [r for r in rows if r["last_date"] >= cutoff]
    hidden = [r for r in rows if r["last_date"] < cutoff]
    if shown_tests is not None:
        keep = set(shown_tests)
        in_window_total = len(shown)
        shown = [r for r in shown if r["test_name"] in keep]
        hidden_by_list = in_window_total - len(shown)
        if n_days is None:
            # Блок без окна и сам является лаб-контекстом: отсылать «в лаб-контекст»
            # некуда. Называем, ЧТО именно отбор выбрасывает — иначе «подмножество»
            # остаётся словом, из которого модель ничего не выведет.
            list_note = (
                f" отобрано {len(shown)} имён из {in_window_total} аналитов в базе; ещё "
                f"{hidden_by_list} в этот блок НЕ попали — это аналиты с качественным "
                f"(текстовым) результатом и аналиты с одной-двумя давними точками."
            ) if hidden_by_list else ""
        else:
            list_note = (
                f" Внутри окна блок показывает НЕ все: отобрано {len(shown)} имён из "
                f"{in_window_total} аналитов со строкой за окно (ещё {hidden_by_list} есть в "
                f"базе, но в этот блок не попали — он показывает ПОДМНОЖЕСТВО канона). "
                f"Полная лабораторная картина живёт в лаб-контексте, не здесь."
            ) if hidden_by_list else ""
    else:
        list_note = ""
    if n_days is None:
        if not list_note:
            return (f"ГРАНИЦА БЛОКА: выше показан ВЕСЬ канон ({len(shown)} аналитов), "
                    f"окна у блока нет.")
        return (f"ГРАНИЦА БЛОКА: окна у блока нет, но{list_note} Поэтому отсутствие "
                f"аналита в блоке НЕ значит «не сдавался» или «не измерено» — оно значит "
                f"«нет числового ряда». Утверждать отсутствие анализа по отсутствию строки "
                f"нельзя.")
    if not hidden:
        if list_note:
            # «Весь канон» здесь было бы ложью: за окно не ушло ничего, но список имён
            # всё равно прячет часть строк. Объявляем именно второй фильтр.
            return (f"ГРАНИЦА БЛОКА: строк старше {n_days} дн. в базе нет.{list_note} "
                    f"Поэтому отсутствие аналита в блоке НЕ значит «не сдавался».")
        return (f"ГРАНИЦА ОКНА: выше показан ВЕСЬ канон ({len(shown)} аналитов) — строк "
                f"старше {n_days} дн. в базе нет, поэтому отсутствие аналита в блоке "
                f"действительно значит, что он не сдавался.")
    dates = sorted({r["last_date"] for r in hidden}, reverse=True)
    dates_txt = ", ".join(dates[:8]) + (f" и ещё {len(dates) - 8} дат" if len(dates) > 8 else "")
    _named = (" — кроме тех, что названы поимённо ниже, в блоке «сдано один раз и не "
              "пересдавалось»" if named_below else "")
    return (f"ГРАНИЦА ОКНА: выше показаны {len(shown)} аналитов со строкой за последние "
            f"{n_days} дн. Ещё у {len(hidden)} аналитов строки СТАРШЕ этой границы "
            f"(заборы: {dates_txt}); их имена здесь не перечислены{_named}. Поэтому отсутствие "
            f"аналита в блоке НЕ значит «не сдавался», «ни разу» или «отсутствует "
            f"полностью» — оно значит «нет данных за {n_days} дн.», и писать надо именно "
            f"так. Утверждать отсутствие анализа по отсутствию строки нельзя.{list_note}")


def declared_boundary(n_days, unjudged: bool = False, **kw) -> str:
    """`canon_window_note`, который НЕ бросает: отказ сборки сам становится объявлением.

    Блок без объявления выглядит полным, поэтому «не собралось» обязано быть сказано
    в тексте, а не только в логе. До 21.09 этот try/except жил копией у каждого
    вызывающего (patient_context, hai_context) — третья копия и завела этот дом.

    `unjudged=True` — добавить строку «НЕ ПРОВЕРЕНО ПОРОГАМИ» (_unjudged_note). Флаг, а не
    умолчание: читатели этой функции — не только врачи. patient_context и hai_context
    кормят чат и агентов утреннего брифа (человек читает их вывод каждый день), а
    generate_constitutions — документ горизонта «от года», куда сиюминутное не входит.
    Включают врачебные контексты: gp_context, wellally_consult, consult_prep,
    hypothesis_consilium_eval."""
    try:
        note = canon_window_note(n_days, **kw)
    except Exception as e:
        log.warning("граница лаб-блока не собрана: %s", e)
        note = ("ГРАНИЦА БЛОКА: объявление не собралось — считай этот список НЕПОЛНЫМ "
                "и не делай выводов об отсутствии анализов.")
    extra = _unjudged_note() if unjudged else ""
    return "\n".join(x for x in (note, extra) if x)


def _unjudged_note() -> str:
    """Вторая граница лаб-блока: аналиты, по которым предохранитель НЕ судил, потому что
    не с чем сравнить (решение владельца 28.09, нить doctor-unjudged-line).

    С нити safety-cannot-judge «судить нечем» не идёт человеку тревогой, а пишется записью
    тенанта (safety_net.CANNOT_JUDGE_KEY). Врачу при этом пропала граница: молчание
    предохранителя по такому аналиту читалось бы как «в норме». Строка нейтральная — без
    служебных слов (отображения, скриптов): врачу нужно «не проверено порогом», а не почему
    у системы так вышло. Нет записи / старая — строки нет (не знаем, а не «всё проверено»);
    отказ чтения — лог, строки нет: блок без неё — прежнее поведение, не ложь новая."""
    try:
        import safety_net as _sn
        with _hdb.get_conn() as conn:
            items = _sn.cannot_judge_open(conn, get_today())
    except Exception as e:  # noqa: BLE001 — строка-дополнение не смеет ронять блок врача
        log.warning("граница «не проверено порогом» не собрана: %s", e)
        return ""
    names = sorted({str(i.get("metric")) for i in items or [] if i.get("metric")})
    if not names:
        return ""
    return ("НЕ ПРОВЕРЕНО ПОРОГАМИ: " + ", ".join(names) + " — для этих анализов не нашлось, "
            "с чем сравнить значение (нет референса или ряд не собран), поэтому отсутствие "
            "тревоги по ним НЕ значит «в норме». Суди по самим значениям.")


def get_lab_series(test_name: str, n_days: int = 730, exclude_pro: bool = True) -> list[dict]:
    """ВСЕ числовые результаты одного теста за n_days, хронологически, с референсом бланка.

    Заведён 2026-09-02 (нить norm-from-documents): safety_net.check_lab_trends звал
    get_recent_labs(365, [name]) и ждал ≥3 точек, а тот отдаёт ОДНУ строку на тест
    (GROUP BY) — трендовые алерты по онкомаркерам не срабатывали никогда; golden-тест
    был зелёным, потому что мок отдавал несколько строк, т.е. лгал о форме функции."""
    _hdb._ensure_lab_table()
    cutoff = str(get_today() - timedelta(days=n_days))
    pro_filter = " AND (source IS NULL OR source NOT LIKE 'instrument:%')" if exclude_pro else ""
    with _hdb.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT test_name, value, unit, date, ref_low, ref_high, source
            FROM lab_results
            WHERE test_name = ? AND date >= ? AND value IS NOT NULL{pro_filter}
            ORDER BY date
        """, (test_name, cutoff)).fetchall()
    return [dict(r) for r in rows]


def get_modal_reference(test_name: str, min_docs: int = 3) -> tuple | None:
    """Модальный (ref_low, ref_high) по РАЗНЫМ документам (source); None, если документов
    с интервалом меньше min_docs — один бланк не свидетель (§17)."""
    _hdb._ensure_lab_table()
    from collections import defaultdict
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT ref_low, ref_high, source FROM lab_results WHERE test_name=? "
            "AND ref_low IS NOT NULL AND ref_high IS NOT NULL", (test_name,)).fetchall()
    spans = defaultdict(set)
    for lo, hi, src in rows:
        spans[(float(lo), float(hi))].add(src or "?")
    if not spans:
        return None
    best = max(spans.items(), key=lambda kv: len(kv[1]))
    return best[0] if len(best[1]) >= min_docs else None


def get_lab_trend(test_name: str, n: int = 6) -> list[dict]:
    """Последние n результатов конкретного теста для построения тренда."""
    _hdb._ensure_lab_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT date, value, value_text, status FROM lab_results
            WHERE test_name = ?
            ORDER BY date DESC LIMIT ?
        """, (test_name, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── тренд через отображение LOINC ────────────────────────────────────────────
# Зачем отдельный вход, а не правка `get_lab_trend`: старый читатель берёт строки
# по НАШЕМУ имени и не возвращает единицу вовсе — потребитель тренда физически не
# знает шкалу. Новый идёт через `lab_name_loinc`, и потому обязан различать то,
# чего имя не различает: состав, материал и шкалу измерения.
# Конверсия единиц не позволяет объединять разные вещества.
#
# Ключ группы — (component, system, property) справочника, а НЕ код: коды,
# различающиеся только МЕТОДОМ («by Automated count» против «by Manual count»),
# обязаны попасть в один тренд, а различающиеся материалом или компонентом — в
# разные. Обе стороны границы под тестом; до этой функции у утверждения был
# только аргумент в докстринге `loinc_match._generic_pick`.
#
# ЧЕГО ЗДЕСЬ НЕТ: тихого отката на `test_name`. Неотображённое имя возвращает
# `points=None`, а не пустой список: пустой список читается как «нет данных» —
# ровно та ложь, из-за которой однажды дыру в данных приписали стрессу. `None`
# ломает наивного потребителя громко, и это намеренно.


def _group_of(terms: dict, code: str):
    """(component, system, property) по коду или None, если кода нет в справочнике."""
    t = terms.get(code)
    if not t:
        return None
    return ((t["component"] or "").lower(), (t["system"] or "").lower(),
            (t["property"] or "").lower())


def _merge_by_scale(mine: list[dict], groups: set, our_name: str, terms: dict):
    """Свести группы, различающиеся ТОЛЬКО шкалой, в целевую — или отказаться.

    Возвращает целевую группу · `None` (сводить НЕЛЬЗЯ, вещества разные) ·
    строку `"no_factor"` (свести можно и нужно, но коэффициента нет).

    Граница проведена по составу и материалу, а не по единице.
    Близкие коэффициенты конверсии разных компонентов не доказывают
    тождество измерений: объединять их по одному имени нельзя.

    Коэффициент берётся ТОЛЬКО из `lab_canon.to_conventional`. Второго дома у
    коэффициентов нет и заводить его нельзя: они физические константы (§9, пункт
    «арифметические факты»), живут в коде законно и уже используются промоутом,
    оракулами и контекстом консилиума.
    """
    import lab_canon
    if len({(g[0], g[1]) for g in groups}) != 1:
        return None                       # разные вещество/материал — не шкала
    targets = set()
    for m in mine:
        _v, conv = lab_canon.to_conventional(our_name, 1.0, m["unit"] or "")
        targets.add(lab_canon.norm_unit(conv or m["unit"] or "").lower())
    if len(targets) != 1:
        return "no_factor"                # правило есть не для всех единиц
    target_unit = targets.pop()
    for m in mine:
        if lab_canon.norm_unit(m["unit"] or "").lower() == target_unit:
            return _group_of(terms, m["loinc_num"])
    return "no_factor"


def trend_members(mappings: list[dict], terms: dict, our_name: str,
                  unit: str | None = None, specimen: str = "blood") -> dict:
    """Какие наши имена образуют ОДИН тренд с этим. Чистая: индекс аргументом.

    Индекс приходит аргументом ровно затем же, зачем в `loinc_match.candidates`:
    границу группировки надо проверять без канона.

    Возвращает {'group', 'members', 'issue'}. `issue` не None означает, что
    тренд строить НЕЛЬЗЯ, а не что он пуст:
      `unmapped`     — имени нет в `lab_name_loinc`;
      `ambiguous`    — имя без единицы попадает в разные группы (пять живых
                       случаев, см. комментарий выше) — выбор за вызывающим;
      `unknown_code` — решение ссылается на код, которого нет в справочнике
                       (расхождение версий, R-8).
    """
    import lab_canon

    def _u(x):
        return lab_canon.norm_unit(x or "").lower()

    mine = [m for m in mappings if m["our_name"] == our_name
            and (specimen is None or m["specimen"] == specimen)]
    if unit is not None:
        mine = [m for m in mine if _u(m["unit"]) == _u(unit)]
    if not mine:
        return {"group": None, "members": [], "issue": "unmapped"}
    groups = {_group_of(terms, m["loinc_num"]) for m in mine}
    if None in groups:
        return {"group": None, "members": [], "issue": "unknown_code"}
    if len(groups) > 1:
        merged = _merge_by_scale(mine, groups, our_name, terms)
        if merged is None:
            return {"group": None, "members": [], "issue": "ambiguous",
                    "groups": sorted(groups)}
        if merged == "no_factor":
            return {"group": None, "members": [], "issue": "no_factor",
                    "groups": sorted(groups)}
        groups = {merged}
    g = groups.pop()
    return {"group": g, "issue": None,
            "members": [m for m in mappings if _group_of(terms, m["loinc_num"]) == g]}


def get_lab_trend_by_component(our_name: str, unit: str | None = None,
                               specimen: str = "blood", n: int = 12) -> dict:
    """Тренд по ВЕЩЕСТВУ через отображение LOINC, а не по нашему имени.

    Первый читатель `lab_name_loinc`. До него таблица заполнялась вердиктами
    владельца, но тренд по ней не строил никто — то есть весь дом имени не был
    доказан в конечном употреблении.

    Точка тренда всегда несёт свою единицу. Если отображения единицы нет,
    строка уходит в unresolved с причиной, не наследуя шкалу соседей.
    """
    import sqlite3
    import lab_canon
    _hdb._ensure_lab_table()
    out = {"our_name": our_name, "group": None, "members": [], "points": None,
           "unresolved": [], "reference_version": None, "issues": [], "n_rows": 0}
    with _hdb.get_conn() as conn:
        _hdb.attach_reference(conn)
        # Число строк считается независимо от отображений: отсутствие измерений
        # и отсутствие карты LOINC требуют разных сообщений.
        out["n_rows"] = conn.execute(
            "SELECT COUNT(*) FROM lab_results WHERE test_name=? AND value IS NOT NULL "
            "AND (source IS NULL OR source NOT LIKE 'instrument:%')",
            (lab_canon.normalize(our_name),)).fetchone()[0]
        # У тенанта без запуска loinc_match таблица отображений может отсутствовать.
        # Пустая карта означает «тренд не построен»; недоступность справочника
        # reference — отдельная ошибка.
        if not conn.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' "
                            "AND name='lab_name_loinc'").fetchone():
            out["issues"].append("no_mapping_table")
            return out
        try:
            mappings = [dict(r) for r in conn.execute(
                "SELECT our_name, unit, specimen, loinc_num FROM lab_name_loinc")]
            codes = sorted({m["loinc_num"] for m in mappings})
            terms = {}
            if codes:
                ph = ",".join("?" * len(codes))
                terms = {r["loinc_num"]: dict(r) for r in conn.execute(
                    f"SELECT loinc_num, component, system, property, loinc_version "
                    f"FROM loinc_terms WHERE loinc_num IN ({ph})", codes)}
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"справочник LOINC недоступен ({exc}); тренд по веществу строить "
                f"нельзя — см. health_db.attach_reference") from exc

        versions = {t.get("loinc_version") for t in terms.values()}
        out["reference_version"] = sorted(v for v in versions if v)[-1] if versions else None
        if len([v for v in versions if v]) > 1:
            out["issues"].append("reference_version_mixed")

        res = trend_members(mappings, terms, our_name, unit, specimen)
        out["group"] = res["group"]
        out["members"] = res["members"]
        if res["issue"]:
            out["issues"].append(res["issue"])
            if res.get("groups"):
                out["groups"] = res["groups"]
            return out

        # Ключ строится в конвенциональной единице с обеих сторон,
        # чтобы отображение и данные встретились независимо от исходной шкалы.
        # Предпочтение — члену, чья единица УЖЕ конвенциональна: он несёт код
        # целевой шкалы, и точка отчитывается тем кодом, в котором её и хранят.
        by_key = {}
        for m in res["members"]:
            raw_u = lab_canon.norm_unit(m["unit"] or "").lower()
            _v, conv = lab_canon.to_conventional(m["our_name"], 1.0, m["unit"] or "")
            conv_u = lab_canon.norm_unit(conv or m["unit"] or "").lower()
            k = (m["our_name"], conv_u)
            if k not in by_key or raw_u == conv_u:
                by_key[k] = m["loinc_num"]
        names = sorted({m["our_name"] for m in res["members"]})
        ph = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT date, test_name, value, unit, status, ref_low, ref_high, source FROM lab_results "
            f"WHERE test_name IN ({ph}) ORDER BY date DESC", names).fetchall()

    # Единицы, про которые отображение вообще что-то знает — в обеих записях,
    # сырой и конвенциональной: строка попадает в `unresolved` только если её
    # единица не встречается НИ в одном виде.
    mapped_units = set()
    for m in mappings:
        if m["our_name"] not in names:
            continue
        mapped_units.add(lab_canon.norm_unit(m["unit"] or "").lower())
        _v, conv = lab_canon.to_conventional(m["our_name"], 1.0, m["unit"] or "")
        mapped_units.add(lab_canon.norm_unit(conv or m["unit"] or "").lower())
    # Конверсию значений применяем, ТОЛЬКО если все группы этого имени — одно
    # вещество в одном материале. Иначе (живой `Calcium`: общий против
    # ионизированного) пересчёт по имени положил бы чужое вещество в тренд.
    own_groups = {_group_of(terms, m["loinc_num"]) for m in mappings
                  if m["our_name"] == our_name}
    scale_only = len({(g[0], g[1]) for g in own_groups if g}) == 1

    points = []
    for r in rows:
        raw_u = lab_canon.norm_unit(r["unit"] or "").lower()
        key = (r["test_name"], raw_u)
        code = by_key.get(key)
        conv_v, conv_u = r["value"], r["unit"]
        if code is None and scale_only and r["unit"]:
            conv_v, conv_u = lab_canon.to_conventional(
                r["test_name"], r["value"], r["unit"])
            code = by_key.get((r["test_name"],
                               lab_canon.norm_unit(conv_u or "").lower()))
        if code:
            converted = lab_canon.norm_unit(conv_u or "").lower() != raw_u
            # референс бланка едет с точкой в ТОЙ ЖЕ шкале (to_conventional_range): читатель
            # тренда по нему различает лаборатории (safety_net._lab_fingerprint)
            lo, hi = r["ref_low"], r["ref_high"]
            if converted:
                lo, hi = lab_canon.to_conventional_range(r["test_name"], lo, hi, r["unit"] or "")
            points.append({"date": r["date"], "value": conv_v, "unit": conv_u,
                           "status": r["status"], "test_name": r["test_name"],
                           "loinc_num": code, "converted": converted,
                           "raw_unit": r["unit"] if converted else None,
                           "ref_low": lo, "ref_high": hi, "source": r["source"]})
        elif key[1] not in mapped_units:
            out["unresolved"].append(
                {"date": r["date"], "value": r["value"], "unit": r["unit"],
                 "test_name": r["test_name"],
                 "reason": "единица не отображена" if r["unit"]
                           else "единица не записана"})
    out["points"] = list(reversed(points[:n]))
    if out["unresolved"]:
        out["issues"].append("unresolved_units")
        log.warning("тренд %s: %d строк вне отображения по единице",
                    our_name, len(out["unresolved"]))
    return out


# Единая граница рендера результата перед промптом LLM.
# Все писатели канона могут иметь разные пути валидации, поэтому выход
# принимает только число, слово из словаря или «н/д». Свободный текст бланка
# не становится инструкцией модели через отдельную f-строку читателя (§19).
_QUAL_RU = {
    "negative": "отрицательно", "not_detected": "не обнаружено",
    "positive": "положительно", "detected": "обнаружено",
    "trace": "следы", "normal": "норма",
    "rare": "единичные", "many": "много",     # микроскопия осадка (2026-08-31)
}


def result_text(row) -> str:
    """Строка канона → человекочитаемый результат. Никогда не отдаёт сырой текст.

    Незнакомое значение НЕ печатается: вместо него «н/д (см. источник)». Это не
    потеря — сырое написание живёт в staging и в самом документе, а вот прошедший
    насквозь текст из документа стал бы частью промпта.
    """
    v = row.get("value") if isinstance(row, dict) else row["value"]
    if v is not None:
        return str(v)
    txt = ((row.get("value_text") if isinstance(row, dict) else row["value_text"]) or "").strip()
    if not txt:
        return "—"
    return _QUAL_RU.get(txt, "н/д (см. источник)")


# Словарь статуса в каноне — буквы бланка (H/L/N), а не слова. Дом словаря — здесь;
# patient_context читает его отсюда (до 26.09 жил там, а медкарте понадобился тот же).
FLAG_WORDS = {"H": "HIGH", "HIGH": "HIGH", "L": "LOW", "LOW": "LOW",
              "CRITICAL": "CRITICAL", "HH": "CRITICAL", "LL": "CRITICAL"}
_DIR = {"HIGH": "↑", "LOW": "↓", "CRITICAL": "‼"}


def flag_direction(r: dict) -> str | None:
    """Стрелка отклонения строки канона: пометка лаборатории, если напечатана; иначе
    сравнение с референсом бланка. Нет ни того, ни другого → None (не «норма»)."""
    stat = (r.get("status") or "").strip().upper()
    if stat in FLAG_WORDS:
        return _DIR[FLAG_WORDS[stat]]
    if stat == "FLAGGED":
        return "~"
    v, lo, hi = r.get("value"), r.get("ref_low"), r.get("ref_high")
    try:
        if v is not None and lo is not None and float(v) < float(lo):
            return "↓"
        if v is not None and hi is not None and float(v) > float(hi):
            return "↑"
    except (TypeError, ValueError):
        return None
    return None


def draw_summaries() -> list[dict]:
    """Сданные анализы коротко — по забору (draw_key: файл + дата), для медкарты.
    Опросники (source instrument:*) в канон кладутся тоже, но это не забор — исключены.
    Считается при чтении из канона: второй копии итогов нет."""
    with _hdb.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='lab_results'").fetchone():
            return []   # анализов ещё не было — таблица рождается при первом
        rows = conn.execute(
            "SELECT date, source, test_name, value, status, ref_low, ref_high FROM lab_results "
            "WHERE date IS NOT NULL AND COALESCE(source,'') NOT LIKE 'instrument:%' "
            "ORDER BY date DESC, test_name").fetchall()
    draws: dict = {}
    for r in rows:
        r = dict(r)
        k = draw_key(r["source"], r["date"])
        label = (r["source"] or "").removeprefix("doc:") or i18n.t("dashboard.labs.source_unknown")
        d = draws.setdefault(k, {"date": r["date"], "file": k[0], "label": label, "n": 0, "flags": []})
        d["n"] += 1
        arrow = flag_direction(r)
        if arrow:
            d["flags"].append(f"{r['test_name']} {arrow}")
    return list(draws.values())


def get_labs_by_date(date_str: str) -> list[dict]:
    """Все результаты за конкретную дату."""
    _hdb._ensure_lab_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT test_name, value, value_text, unit, status
            FROM lab_results WHERE date=?
            ORDER BY test_name
        """, (date_str,)).fetchall()
    return [dict(r) for r in rows]


def get_lab_history(days: int = 730) -> list[dict]:
    """
    Все лабные результаты за последние N дней, хронологически.
    Используется hypothesis_consilium_eval для eval data package.
    """
    _hdb._ensure_lab_table()
    from datetime import timedelta
    cutoff = str(get_today() - timedelta(days=days))
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT date, test_name, value, value_text, unit, ref_low, ref_high, status
            FROM lab_results
            WHERE date >= ?
              AND (source IS NULL OR source NOT LIKE 'instrument:%')
            ORDER BY date DESC, test_name
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def compute_bank_refs(min_docs: int = 3) -> dict:
    """Референс каждого аналита = МОДА напечатанного интервала бланка по ≥min_docs РАЗНЫМ
    документам (вид 1, CLSI: референс принадлежит методу лаборатории, не справочнику).
    Возвращает {canon: [lo, hi, unit, n_docs]}. Аналит без моды в словарь не попадает —
    читатели обязаны уметь «референс не установлен». Нить norm-from-documents 2026-09-02:
    до этого здесь был литерал LAB_REFS_CANONICAL «для мужчины» без источника (22 строки,
    ключ 'Cholesterol' — не канон)."""
    from collections import defaultdict
    import lab_canon
    spans: dict = defaultdict(lambda: defaultdict(set))
    units: dict = defaultdict(lambda: defaultdict(int))
    with _hdb.get_conn() as conn:
        # Не создаём таблицу здесь: init_db мигрирует lab_results позже (specimen/method),
        # преждевременный CREATE ломал _migrate_lab_method. Нет таблицы — нет моды.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lab_results)")}
        if not {"ref_low", "ref_high", "unit", "source"} <= cols:  # нет таблицы/колонок — нет моды
            return {}
        rows = conn.execute(
            "SELECT test_name, ref_low, ref_high, unit, source FROM lab_results "
            "WHERE ref_low IS NOT NULL AND ref_high IS NOT NULL "
            "AND (source IS NULL OR source NOT LIKE 'instrument:%')").fetchall()
    for tn, lo, hi, unit, src in rows:
        m = lab_canon.normalize(tn)
        try:
            key = (float(lo), float(hi), lab_canon.norm_unit(unit or "").lower())
        except (TypeError, ValueError):
            continue
        spans[m][key].add(src or "?")
        units[m][unit or ""] += 1
    out = {}
    for m, sp in spans.items():
        (lo, hi, _u), docs = max(sp.items(), key=lambda kv: len(kv[1]))
        if len(docs) >= min_docs:
            unit = max(units[m].items(), key=lambda kv: kv[1])[0]
            out[m] = [lo, hi, unit, len(docs)]
    return out


def get_lab_refs() -> dict:
    """Референсные интервалы: КЭШ моды бланков (system_config.lab_refs, пишет
    health_db._refresh_lab_refs на init_db), формат {test_name: (min, max, unit)} — совместим
    с читателями. Пусто → {} (литерала-резерва больше нет: число без источника хуже пустоты)."""
    stored = _hdb.get_config("lab_refs")
    if stored and isinstance(stored, dict):
        return {k: tuple(v[:3]) if isinstance(v, list) else v for k, v in stored.items()}
    return {}


def get_lab_refs_meta() -> dict:
    """{test_name: n_docs} и дата расчёта — провенанс кэша референсов."""
    stored = _hdb.get_config("lab_refs") or {}
    meta = _hdb.get_config("lab_refs_meta") or {}
    return {"n_docs": {k: (v[3] if isinstance(v, list) and len(v) > 3 else None) for k, v in stored.items()},
            "computed": meta.get("computed"), "min_docs": meta.get("min_docs")}


def get_effective_lab_schedule() -> dict:
    """Возвращает {test_name: {interval_days, priority, source, note}} из БД."""
    with _hdb.get_conn() as conn:
        _cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_monitoring_schedule)")]
        if "interval_doctor_days" in _cols and "interval_stable_days" in _cols:
            rows = conn.execute(
                "SELECT test_name, "
                "COALESCE(interval_doctor_days, interval_stable_days, interval_days) AS interval_days, "
                "interval_stable_days, interval_doctor_days, priority, source, note "
                "FROM lab_monitoring_schedule"
            ).fetchall()
        else:  # БД без two-tier миграции (напр. тестовая фикстура)
            rows = conn.execute(
                "SELECT test_name, interval_days, NULL AS interval_stable_days, "
                "NULL AS interval_doctor_days, priority, source, note "
                "FROM lab_monitoring_schedule"
            ).fetchall()
    # two-tier (2026-07-01): interval_days = effective = doctor ?? stable.
    # doctor (рекомендация врача) доминирует над stable-дефолтом.
    return {
        r["test_name"]: {
            "interval_days":        r["interval_days"],
            "interval_stable_days": r["interval_stable_days"],
            "interval_doctor_days": r["interval_doctor_days"],
            "priority":             r["priority"],
            "source":               r["source"],
            "note":                 r["note"],
        }
        for r in rows
    }


def build_lab_history_context(min_points: int = 3, max_hist: int = 8,
                              horizon_days: int | None = None) -> str:
    """Полная лаб-история с пометкой давности по effective-сроку валидности.

    Для консилиума/конституций: видят И тренды (многолетние), И различают
    ТЕКУЩЕЕ (в пределах срока аналита) vs УСТАРЕЛО (нет актуальных данных).
    Срок берётся из get_effective_lab_schedule (doctor ?? stable).

    min_points гейтит ТОЛЬКО тренд-историю: аналит со свежим (в пределах срока)
    значением показывается всегда, даже при 1–2 точках. Иначе потребитель
    конфабулирует «не измерено» (инцидент 09.07). PRO-опросники (unit=score*)
    исключены — их держит build_specialized_context.

    horizon_days (конституции, решение владельца 2026-09-25): режим «устройство, не
    конъюнктура» — только аналиты, чей ряд (≥ min_points точек) тянется от первой до
    последней точки не короче горизонта; статуса ТЕКУЩЕЕ/УСТАРЕЛО нет, свежее значение без
    ряда не показывается. Остальные читатели зовут без него — поведение прежнее."""
    from collections import defaultdict
    import lab_canon

    today = get_today()
    sched = get_effective_lab_schedule()
    sched_norm = {lab_canon.normalize(k): v for k, v in sched.items()}

    with _hdb.get_conn() as conn:
        _cols = {c[1] for c in conn.execute("PRAGMA table_info(lab_results)")}
        _extra = "".join(f", {c}" for c in ("specimen", "method") if c in _cols)
        rows = conn.execute(
            f"SELECT test_name, date, value, unit{_extra} FROM lab_results "
            "WHERE value IS NOT NULL "
            # Самоотчёт (опросники, source instrument:*) — не анализ: в лаб-историю не идёт
            # по источнику, а не только по единице score* ниже. Решение владельца 27.09:
            # в конституцию не идёт субъективное; единица — эвристика, источник — факт.
            "AND COALESCE(source, '') NOT LIKE 'instrument:%' "
            "ORDER BY test_name, date, (unit IS NULL OR unit='')"  # с единицей вперёд
        ).fetchall()

    # Материал берётся из КОЛОНКИ, а не выводится из единицы (2026-07-31, шаг 8).
    # Прежде `lab_canon.specimen_key` угадывал его по единице и приписывал к ИМЕНИ
    # (`Methylmalonic_acid_serum`), пока промоут писал тот же материал в колонку
    # словом `blood`. Два дома одного факта с разными словарями: колонка говорила
    # «кровь», имя — «сыворотка». Дом остался один — колонка; здесь она читается.
    # Разделяем ТОЛЬКО те аналиты, у которых материалов в данных ДЕЙСТВИТЕЛЬНО
    # несколько: список многоматериальных больше не нужен, его отвечают сами данные.
    _mats = defaultdict(set)
    for r in rows:
        _mats[lab_canon.normalize(r["test_name"])].add((r["specimen"] or "").strip()
                                                       if "specimen" in r.keys() else "")

    series = defaultdict(list)
    raw_names = defaultdict(set)     # gkey → сырые test_name: объявление считает по канону
    seen = set()
    for r in rows:
        _k = r.keys()
        spec = (r["specimen"] or "").strip() if "specimen" in _k else ""
        meth = (r["method"] or "").strip() if "method" in _k else ""
        # Дедуп по ТОМУ ЖЕ ключу, что у канона. Без материала и метода этот
        # читатель молча выбрасывал бы второе измерение того же дня — например
        # тестостерон иммуноанализом при уже взятом в тот же день масс-спектрометрией.
        key = (r["test_name"], r["date"], spec, meth)
        if key in seen:
            continue
        seen.add(key)
        _canon0 = lab_canon.normalize(r["test_name"])
        gkey = (f"{r['test_name']} [{spec}]"
                if spec and len(_mats[_canon0]) > 1 else r["test_name"])
        series[gkey].append((r["date"], r["value"], r["unit"] or ""))
        raw_names[gkey].add(r["test_name"])

    lines = []
    shown_raw = set()
    for name in sorted(series):
        pts = series[name]
        canon = lab_canon.normalize(name)
        # PRO-опросники (<id>_<подшкала>, unit=score*) — отдельный канал
        # build_specialized_context, НЕ лаб-секция (иначе засоряют при снятии порога точек).
        if "score" in (pts[-1][2] or "").lower():
            continue
        # Единицы точек к канону (to_conventional) → тренд сравним: CRP mg/L vs mg/dL и
        # Phosphorus mmol/l vs mg/dL не дают мнимого скачка/инверсии в промпте консилиума.
        cpts = [(d, *lab_canon.to_conventional(canon, v, u)) for d, v, u in pts]
        last_d, last_v, last_u = cpts[-1]
        interval = (sched_norm.get(canon) or {}).get("interval_days") or 180
        try:
            age = (today - date.fromisoformat(last_d)).days
        except Exception:
            age = None
        # min_points ограничивает только тренд. Свежее текущее значение
        # показывается даже при коротком ряде: иначе отсутствие тренда
        # ошибочно превращается в отсутствие измерений.
        is_current = age is not None and age <= interval
        if horizon_days is not None:
            try:
                span = (date.fromisoformat(last_d[:10]) - date.fromisoformat(cpts[0][0][:10])).days
            except Exception:
                continue   # silent-ok: дата не разбирается → ряд не доказан, в режим горизонта не идёт
            if len(pts) < min_points or span < horizon_days:
                continue
            # Годовые средние сохраняют начало ряда независимо от частоты заборов.
            by_year = defaultdict(list)
            for d, v, _u in cpts:
                try:
                    by_year[d[:4]].append(float(v))
                except (TypeError, ValueError):
                    continue   # silent-ok: нечисловое значение в годовое среднее не входит
            unit = cpts[-1][2]
            hist = "; ".join(f"{y}: {round(sum(vs) / len(vs), 3)}" + (f" (n={len(vs)})" if len(vs) > 1 else "")
                             for y, vs in sorted(by_year.items()))
            lines.append(f"{canon} [{unit}]: {len(pts)} точек за {span // 365} г. {span % 365 // 30} мес. "
                         f"| по годам: {hist}")
            shown_raw |= raw_names[name]
            continue
        if len(pts) < min_points and not is_current:
            continue
        if age is None:
            tag = "?"
        elif age <= interval:
            tag = f"ТЕКУЩЕЕ ({age}д, срок {interval}д)"
        elif age <= 2 * interval:
            tag = f"УСТАРЕВАЕТ ({age}д > срок {interval}д)"
        else:
            tag = f"УСТАРЕЛО ({age}д, нет актуальных данных при сроке {interval}д)"
        hist = "; ".join(f"{d}:{v}{u}" for d, v, u in cpts[:-1][-max_hist:])
        lines.append(f"{canon}: последнее {last_d}={last_v}{last_u} [{tag}]"
                     + (f" | тренд: {hist}" if hist else ""))
        shown_raw |= raw_names[name]
    if not lines:
        return ""
    # Блок отбирает ДВАЖДЫ и до 21.09 молчал об обоих отборах: `value IS NOT NULL`
    # выбрасывает все качественные результаты, `min_points` — аналиты с 1–2 давними
    # точками. Замер 21.09 на живом каноне: блок на 16.7 КБ ехал в консилиум и в
    # конституции без слова о том, что он срез (context_declares_its_boundary).
    note = declared_boundary(None, shown_tests=sorted(shown_raw))
    return "\n".join(lines) + ("\n" + note if note else "")


# ЗАБОР БЕЗ ПОВТОРА
# Окно промпта и минимальная длина ряда могут вместе скрыть разовое измерение.
# Старый результат без повтора остаётся единственным известным, поэтому
# его отсутствие в контексте нельзя выдавать за отсутствие обследования.
# Единица показа — забор: счёт по исходам и имена результатов вне референса
# либо качественно положительных. Это сохраняет историю без полного списка.
#
# ПОРОГА ПОКАЗА ЗДЕСЬ НЕТ, И ЭТО РЕШЕНИЕ, А НЕ ПРОПУСК. Множество не может расти
# само: аналит покидает его в тот день, когда его пересдают. Порог был бы защитой от
# роста, которого механизм не допускает. Вместо порога — датчик у соседа
# (`integrity_tests.check_unrepeated_draw_not_swamping`): если исторический блок
# перерастёт текущую историю, человек узнает об этом, а не промпт молча распухнет.
_QUAL_POSITIVE = {"positive", "detected"}
# Граница словаря названа: «следы», «единичные», «много» — это микроскопия осадка, а
# не находка. Они едут в счётчик. Тревожным считается только прямое «обнаружено».


def _ref_ru(lo, hi) -> str:
    """Референс словами. Односторонний интервал бланка (`< 50`, `> 4.3`) хранится
    как NULL на второй границе, и наивная f-строка печатала «норма None–50.0»:
    в промпте `None` читается как ЗНАЧЕНИЕ, а не как «границы нет»."""
    if lo is None and hi is None:
        return "референс не установлен"
    if lo is None:
        return f"норма до {hi}"
    if hi is None:
        return f"норма от {lo}"
    return f"норма {lo}–{hi}"


def unrepeated_draw(rows, cutoff: str) -> dict:
    """ЧИСТАЯ логика блока. rows — строки канона (dict/Row) с полями
    test_name, date, value, value_text, unit, ref_low, ref_high.

    Возвращает {dates, total, out_of_ref, qual_positive, normal, no_ref, qual_other}.
    Кандидат — последняя строка имени, у которого НЕТ замера свежее `cutoff`.
    """
    last: dict = {}
    for r in rows:
        n, d = r["test_name"] or "", r["date"] or ""
        if d > last.get(n, ""):
            last[n] = d
    sel = [r for r in rows
           if last.get(r["test_name"] or "", "") < cutoff
           and (r["date"] or "") == last.get(r["test_name"] or "", "")]

    out, pos, normal, no_ref, qual_other = [], [], 0, 0, 0
    for r in sel:
        v, lo, hi = r["value"], r["ref_low"], r["ref_high"]
        if v is None:
            txt = (r["value_text"] or "").strip()
            if txt in _QUAL_POSITIVE:
                pos.append((r["test_name"], _QUAL_RU.get(txt, txt)))
            else:
                qual_other += 1
            continue
        if lo is None and hi is None:
            no_ref += 1
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            no_ref += 1
            continue
        if (lo is not None and fv < lo) or (hi is not None and fv > hi):
            out.append((r["test_name"], fv, r["unit"] or "", lo, hi))
        else:
            normal += 1
    dates = sorted({r["date"] for r in sel if r["date"]})
    return {"dates": dates, "total": len(sel), "out_of_ref": out,
            "qual_positive": pos, "normal": normal, "no_ref": no_ref,
            "qual_other": qual_other}


def build_unrepeated_draw_context(n_days: int = PROMPT_WINDOW_DAYS) -> str:
    """Блок «сдано один раз и не пересдавалось» для контекстов LLM."""
    _hdb._ensure_lab_table()
    cutoff = str(get_today() - timedelta(days=n_days))
    with _hdb.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT test_name, date, value, value_text, unit, ref_low, ref_high "
            "FROM lab_results "
            "WHERE (source IS NULL OR source NOT LIKE 'instrument:%')")]
    d = unrepeated_draw(rows, cutoff)
    if not d["total"]:
        return ""
    span = d["dates"][0] if len(d["dates"]) == 1 else f"{d['dates'][0]}..{d['dates'][-1]}"
    lines = [
        f"СДАНО ОДИН РАЗ И НЕ ПЕРЕСДАВАЛОСЬ ({span}), аналитов — {d['total']}, все "
        f"вне окна в {n_days} дней. Отсутствие пересдачи НЕ значит «в норме» и НЕ "
        f"значит «не сдавалось» — значит, что свежее этого ничего нет.",
        f"  в норме бланка: {d['normal']}; без референса: {d['no_ref']}; "
        f"качественных без находки: {d['qual_other']}",
    ]
    if d["qual_positive"]:
        lines.append("  НАЙДЕНО (качественно, {}): ".format(len(d["qual_positive"]))
                     + "; ".join(f"{n} — {t}" for n, t in d["qual_positive"]))
    if d["out_of_ref"]:
        lines.append(f"  вне референса бланка ({len(d['out_of_ref'])}): " + "; ".join(
            f"{n}={v}{(' ' + u) if u else ''} ({_ref_ru(lo, hi)})"
            for n, v, u, lo, hi in d["out_of_ref"]))
    return "\n".join(lines)


def draw_key(source, date_str):
    """conit-идентификатор забора для co-draw: (basename без doc:-префикса, дата).
    Валидировано 08.07 на данных: один и тот же файл больницы на разных датах = разные заборы;
    prefix-варианты одного документа на одной дате = один. plan Фаза 1 co-draw."""
    import os as _os
    import re as _re
    return (_os.path.basename(_re.sub(r"^doc:", "", (source or "").strip())), date_str)


def build_codraw_context(days: int = 120, min_cluster: int = 2) -> str:
    """Срез по заборам за последние `days` дней (источник + дата).

    Кластер содержит ≥min_cluster новых отклонений относительно собственных
    предыдущих измерений; хронический фон исключается. Совместный образец
    позволяет рассматривать кластер как событие, а не независимые причины.
    Логика — чистая `_codraw_clusters`.
    """
    from datetime import timedelta
    cutoff = (get_today() - timedelta(days=days)).isoformat()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT test_name, value, unit, date, source, ref_low, ref_high "
            "FROM lab_results WHERE value IS NOT NULL ORDER BY date"
        ).fetchall()
    clusters = _codraw_clusters(rows, cutoff, min_cluster)
    if not clusters:
        return ""
    lines = [f"  Забор {d} ({base}): {len(abn)} ОСТРЫХ отклонений (новых vs baseline) в ОДНОМ "
             "образце — рассмотри как ЕДИНОЕ событие: " + "; ".join(abn)
             for base, d, abn in clusters]
    return ("СРЕЗ ПО ЗАБОРАМ (co-draw): ОСТРЫЕ отклонения (новые vs собственный baseline),\n"
            "сошедшиеся в одном образце — вероятнее одно событие/состояние забора, чем\n"
            "независимые болезни. Не трактуй как отдельные процессы без различающего теста\n"
            "(чистый повтор натощак). Хронически аномальные исключены.\n"
            + "\n".join(lines))


def _codraw_clusters(rows, cutoff, min_cluster=2):
    """Чистая/тестируемая логика co-draw: rows [{test_name,value,unit,date,source,ref_low,
    ref_high}] → [(base, date, [строки острых отклонений])] для заборов в окне ≥cutoff с
    ≥min_cluster ОСТРЫМИ (новыми vs собственный предыдущий замер) отклонениями. (в) хронически
    аномальные исключены. Работает с sqlite.Row И dict (посаженные кейсы). plan Фаза 1 co-draw."""
    import lab_canon
    from collections import defaultdict

    def _in_ref(v, lo, hi):
        # Односторонний референс (≥60 у HDL, ≤5.7 у HbA1c) законен: NULL-граница = нет
        # границы. BL-CODRAW-1 (2026-08-29): `float(lo) <= float(v) <= float(hi)` при v<lo
        # НЕ вычислял float(hi) (короткое замыкание) → False при hi=NULL, а стрелка ниже
        # падала на float(None); консилиум месяц шёл без ко-дро (silent-ok в eval).
        try:
            v = float(v)
            lo = None if lo is None else float(lo)
            hi = None if hi is None else float(hi)
        except (TypeError, ValueError):
            return None
        if lo is None and hi is None:
            return None
        return (lo is None or lo <= v) and (hi is None or v <= hi)

    series = defaultdict(list)   # canon -> [(date, in_ref|None)]
    for r in rows:
        canon = lab_canon.normalize(r["test_name"])
        series[canon].append((r["date"], _in_ref(r["value"], r["ref_low"], r["ref_high"])))
    for canon in series:
        series[canon].sort(key=lambda x: x[0])

    def _acute(canon, d):
        prior = [ir for (dt, ir) in series[canon] if dt < d and ir is not None]
        return (not prior) or (prior[-1] is True)

    draws = defaultdict(list)
    for r in rows:
        if r["date"] >= cutoff:
            draws[draw_key(r["source"], r["date"])].append(r)
    out = []
    for (base, d), items in sorted(draws.items(), key=lambda kv: kv[0][1], reverse=True):
        abn = []
        for r in items:
            if _in_ref(r["value"], r["ref_low"], r["ref_high"]) is False:
                canon = lab_canon.normalize(r["test_name"])
                if _acute(canon, d):
                    lo, hi = r["ref_low"], r["ref_high"]
                    arrow = "↑" if hi is not None and float(r["value"]) > float(hi) else "↓"
                    norm = (f"{lo}-{hi}" if lo is not None and hi is not None
                            else f"≥{lo}" if hi is None else f"≤{hi}")
                    abn.append(f"{canon} {r['value']}{r['unit'] or ''}{arrow}(норма {norm})")
        if len(abn) >= min_cluster:
            out.append((base, d, abn))
    return out


def build_specialized_context(max_abnormal: int = 12) -> str:
    """Сводка specialized_lab_results (микробиом/аутоантитела/метаболомика/…) для
    консилиума/конституций (BL-LAB-CANON-2 T4). Ограничивает объём контекста:
    счётчики по panel_type + аномальные (вне референса) + ЯВНЫЙ тег давности.
    Без даты старый результат можно принять за текущий (stale-copy).
    Устойчива к отсутствию таблицы.

    Модель таблицы, дискриминатор panel_type и что значит «ждёт сведения»:
    docs/reference/specialized_lab_results.md."""
    from collections import Counter
    with _hdb.get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(specialized_lab_results)")]
        if not cols:
            return ""
        rows = conn.execute(
            "SELECT date, panel_type, analyte_raw, value, unit, ref_low, ref_high "
            "FROM specialized_lab_results").fetchall()
    if not rows:
        return ""
    # РАЗВОДИМ ДВА РАЗНЫХ ПОСТОЯЛЬЦА (2026-08-01, работа B).
    #
    # Здесь могут временно находиться строки, ожидающие приёма каноном.
    # Их нельзя представлять как окончательно отнесённые к спец-панелям.
    # Биохимия крови под этикеткой «не биохимия крови» и «не текущее состояние» —
    # это хуже, чем её отсутствие: §16 ровно про читателя, который получает
    # чужой домен и не падает.
    #
    # Признак берётся из вердикта человека, а не из нового поля: у строки уже
    # есть класс, а у класса — дом. Второе поле было бы вторым домом того же
    # знания.
    # ТРИ постояльца, не два (2026-08-05, находка §16-с-изнанки). Клинически
    # показываем ТОЛЬКО класс с ЯВНЫМ вердиктом дом=specialized. Дом=canon —
    # ждёт сведения имени (инженерный счётчик). Вердикта НЕТ — fail-closed:
    # класс новый, дом решает человек; в клинику он не едет ровно как в канон
    # не едет (промоут §13). Прежний фильтр `!= "canon"` пускал неверди́кченный
    # класс (кириллич. онкомаркеры, опечатка, новый бланк) в клинический
    # заголовок — тот же дефект, что 01.08, но для несуждённого класса.
    verdicts = domain_verdicts()
    waiting = [r for r in rows if verdicts.get(r["panel_type"]) == "canon"]
    unjudged = [r for r in rows if verdicts.get(r["panel_type"]) is None]
    rows = [r for r in rows if verdicts.get(r["panel_type"]) == "specialized"]
    if not rows and not waiting and not unjudged:
        return ""
    by_panel = Counter(r["panel_type"] or "?" for r in rows)
    dates = sorted({r["date"] for r in rows if r["date"]})
    abnormal = []
    for r in rows:
        v, lo, hi = r["value"], r["ref_low"], r["ref_high"]
        try:
            if v is not None and lo is not None and hi is not None and not (lo <= float(v) <= hi):
                abnormal.append(f"{r['analyte_raw']}={v}{r['unit'] or ''} (норма {lo}–{hi})")
        except (TypeError, ValueError):
            pass
    span = f"{dates[0]}..{dates[-1]}" if dates else "?"
    lines = []
    if rows:
        lines += [f"Спец-панели вне биохимии крови ({span} — ИСТОРИЧЕСКОЕ, не текущее состояние):",
                  "  " + "; ".join(f"{p}×{n}" for p, n in by_panel.most_common())]
        if abnormal:
            lines.append(f"  Вне референса ({len(abnormal)}): " + "; ".join(abnormal[:max_abnormal]))
    if waiting:
        # Инженерная строка, НЕ клиническая: это очередь сведения имён, а не
        # результаты. Печатается счётчиком без значений — чтобы долг был виден
        # человеку и при этом не выглядел данными для суждения о пациенте.
        lines.append(f"  [не клинический контекст] ждут сведения имени к канону: "
                     f"{len(waiting)} строк(и) — в лабораторный вывод не входят")
    if unjudged:
        # Класс без вердикта человека — виден как ДОЛГ, но не как клинический
        # факт (fail-closed, находка 2026-08-05). Ночной триаж
        # `check_lab_class_verdicts_complete` эскалирует его владельцу.
        lines.append(f"  [не клинический контекст] класс без вердикта человека: "
                     f"{len(unjudged)} строк(и) — в лабораторный вывод не входят (дом решает человек)")
    return "\n".join(lines)


def upsert_monitoring_rule(
    test_name: str,
    interval_days: int,
    priority: str = "medium",
    source: str = "default",
    effective_from: str = None,
    note: str = None,
) -> None:
    """Upsert расписания мониторинга. default не перебивает encounter/manual."""
    # Приоритет: manual > encounter > default
    # manual не перезаписывается ничем; encounter не перезаписывается manual'ом (уже стоит)
    _PRIORITY = {"manual": 2, "encounter": 1, "default": 0}

    def _src_rank(s: str) -> int:
        if s == "manual":
            return 2
        if s.startswith("encounter"):
            return 1
        return 0

    with _hdb.get_conn() as conn:
        existing = conn.execute(
            "SELECT source FROM lab_monitoring_schedule WHERE test_name=?",
            (test_name,)
        ).fetchone()
        if existing and _src_rank(existing[0]) > _src_rank(source):
            return  # не перезаписываем более приоритетное правило
        conn.execute("""
            INSERT INTO lab_monitoring_schedule
                (test_name, interval_days, priority, source, effective_from, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(test_name) DO UPDATE SET
                interval_days  = excluded.interval_days,
                priority       = excluded.priority,
                source         = excluded.source,
                effective_from = excluded.effective_from,
                note           = excluded.note,
                updated_at     = datetime('now')
        """, (test_name, interval_days, priority, source, effective_from, note))


def get_lab_format_by_name(name: str) -> dict | None:
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, parser_type FROM lab_formats WHERE name=?",
            (name,)
        ).fetchone()
    return dict(row) if row else None


def get_lab_format_by_id(format_id: int) -> dict | None:
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, parser_type FROM lab_formats WHERE id=?",
            (format_id,)
        ).fetchone()
    return dict(row) if row else None


def get_confirmed_aliases(format_id: int, conn=None) -> dict:
    """Возвращает {raw_name: canonical} для confirmed aliases формата.

    `conn` — соединение вызывающего (2026-07-31). Без него берётся БД текущего
    процесса, и это верно для промоута, но НЕ для датчика целостности: тот обходит
    тенантов read-only, и словарь имён обязан читаться из БД ТОГО ЖЕ тенанта.
    Иначе алиасы владельца применились бы к строкам партнёра — тот же класс, что
    захардкоженный OWNER_CHAT_ID в нотификаторах (гипотезы партнёра ушли владельцу).
    """
    sql = ("SELECT raw_name, canonical FROM lab_name_aliases "
           "WHERE format_id=? AND confirmed=1 ORDER BY length(raw_name) DESC")
    if conn is not None:
        return {r[0]: r[1] for r in conn.execute(sql, (format_id,)).fetchall()}
    with _hdb.get_conn() as own:
        rows = own.execute(sql, (format_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def confirmed_alias_conflicts(conn=None) -> list[tuple]:
    """Сырые имена, которым РАЗНЫЕ форматы дали разные канонические имена.

    Пусто — значит глоссарий можно читать как один словарь. Непусто — значит
    вопрос «как называется этот аналит» имеет два ответа человека, и выбирать
    между ними машине нельзя (§13: оракул — человек).
    Замер 31.07: 66 подтверждённых имён, конфликтов 0.
    """
    sql = ("SELECT LOWER(raw_name), format_id, canonical FROM lab_name_aliases "
           "WHERE confirmed=1")
    if conn is not None:
        rows = conn.execute(sql).fetchall()
    else:
        with _hdb.get_conn() as own:
            rows = own.execute(sql).fetchall()
    by = {}
    for raw, fid, canon in rows:
        by.setdefault(raw, set()).add((fid, canon))
    return sorted((raw, sorted(v)) for raw, v in by.items()
                  if len({c for _f, c in v}) > 1)


def confirmed_alias_targets(conn=None) -> set:
    """Множество канонических имён, на которые указывает подтверждённый глоссарий.

    Отдельный вход, а не SELECT в датчике: `lab_name_aliases` — таблица этого
    домена, и запрос к ней из `integrity_tests` был бы вторым домом знания о её
    форме (дубль-гейт назвал это на коммите, и был прав).
    """
    sql = "SELECT DISTINCT canonical FROM lab_name_aliases WHERE confirmed=1"
    if conn is not None:
        rows = conn.execute(sql).fetchall()
    else:
        with _hdb.get_conn() as own:
            rows = own.execute(sql).fetchall()
    return {r[0] for r in rows if r[0]}


def analyte_norm_verdicts(conn=None, today=None) -> dict:
    """{канон аналита: verdict} из `analyte_norm_verdicts` — только ЖИВЫЕ (review_at ≥ today).

    Истёкший вердикт не возвращается по построению: датчик покрытия снова видит
    аналит, и это гаситель, а не дефект (§18 — вердикт не переживает предмет).
    `today` — seam для тестов; умолчание — get_today()."""
    from _time_inject import get_today
    today = str(today or get_today())
    sql = "SELECT analyte, verdict FROM analyte_norm_verdicts WHERE review_at >= ?"
    try:
        if conn is not None:
            rows = conn.execute(sql, (today,)).fetchall()
        else:
            with _hdb.get_conn() as own:
                rows = own.execute(sql, (today,)).fetchall()
    except Exception:  # noqa: BLE001 — таблицы нет (урезанное окружение/старый тенант)
        return {}
    return {r[0]: r[1] for r in rows if r[0]}


def set_analyte_norm_verdict(analyte: str, verdict: str, rationale: str, oracle: str,
                             review_at: str, decided_on: str | None = None) -> str:
    """Записать/заменить вердикт о норме аналита. Имя нормализуется через lab_canon
    (один дом идентичности); вид, непустота и review_at > decided_on — CHECK в SQLite,
    здесь только нормализация. Возвращает канон аналита."""
    import lab_canon
    from _time_inject import get_today
    canon = lab_canon.normalize(analyte)
    with _hdb.get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO analyte_norm_verdicts "
            "(analyte, verdict, rationale, oracle, decided_on, review_at) VALUES (?,?,?,?,?,?)",
            (canon, verdict, rationale, oracle, str(decided_on or get_today()), str(review_at)))
    return canon


def domain_verdicts(conn=None) -> dict:
    """{класс строки: дом} из `lab_domain_verdicts`. Дом ∈ {canon, specialized}.

    Читатель живёт ЗДЕСЬ, а не у писателей, по построению (вердикт владельца 3):
    `labs_db` не импортирует ничего из `lab_*`, поэтому оба писателя канона могут
    спрашивать вердикт, не узнав друг о друге. Прежде общее знание держалось на
    импорте писателя писателем — и разъехалось молча.

    Класса НЕТ в ответе = вердикта нет. Это находка, а не умолчание: новый класс
    обязан краснеть (`integrity_tests`), а не проваливаться в дефолт.
    """
    sql = "SELECT panel_type, home FROM lab_domain_verdicts"
    try:
        if conn is not None:
            rows = conn.execute(sql).fetchall()
        else:
            with _hdb.get_conn() as own:
                rows = own.execute(sql).fetchall()
    except Exception:  # noqa: BLE001 — таблицы нет (урезанное окружение/старый тенант)
        return {}
    return {r[0]: r[1] for r in rows if r[0]}


def domain_home(panel_type: str, conn=None) -> str | None:
    """Дом класса по вердикту человека. None = вердикта нет, решать не машине."""
    return domain_verdicts(conn=conn).get(panel_type)


def get_confirmed_aliases_all(conn=None) -> dict:
    """{raw_name.lower(): canonical} по ВСЕМ форматам — глоссарий для промоута.

    Зачем отдельно от `get_confirmed_aliases(format_id)`: у строки в
    `lab_results_staging` формата НЕТ. Распознаватель кладёт строки из любого
    бланка в одну таблицу, и промоут не может спросить «какого ты формата».
    Раньше он спрашивал глоссарий с литералом `format_id=0` — формата с таким
    id не существует, поэтому 66 подтверждений человека не влияли ни на одну
    строку (F-14). Не «промоут мёртв», а «промоут структурно глух к человеку».

    Конфликтующие имена ВЫБРАСЫВАЮТСЯ, а не разрешаются большинством: два
    разных ответа человека на один вопрос — это не шум, который можно усреднить.
    Молча выбросить тоже нельзя, поэтому громко (§14) и с датчиком-читателем
    (`integrity_tests.check_glossary_conflicts`).
    """
    sql = ("SELECT LOWER(raw_name), canonical FROM lab_name_aliases "
           "WHERE confirmed=1 ORDER BY length(raw_name) DESC")
    if conn is not None:
        rows = conn.execute(sql).fetchall()
    else:
        with _hdb.get_conn() as own:
            rows = own.execute(sql).fetchall()
    out = {r[0]: r[1] for r in rows}
    for raw, variants in confirmed_alias_conflicts(conn):
        out.pop(raw, None)
        log.warning("глоссарий: имя %r имеет РАЗНЫЕ канонические имена в разных "
                     "форматах %s — выброшено из словаря, решает человек",
                     raw, variants)
    return out


def get_all_canonical_names() -> list[str]:
    """Все уникальные canonical-имена в lib (для fuzzy-matching)."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT canonical FROM lab_name_aliases WHERE confirmed=1"
        ).fetchall()
    return [r[0] for r in rows]


def get_all_lab_dates() -> list[str]:
    """Все даты, по которым есть анализы."""
    _hdb._ensure_lab_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM lab_results ORDER BY date"
        ).fetchall()
    return [r["date"] for r in rows]


# ── Канонические пороги свежести лабораторных тестов ────────────────────────
# Переселено из gp_agent (2026-06-28, поток C): источник для health_db._seed_data_freshness
# (seed в lab_monitoring_schedule) и gp_context._check_lab_freshness. Дом — слой данных,
# чтобы ядро не импортировало высокоуровневый gp_agent (обратная зависимость).
# Если данные устарели — агент обязан это видеть и не делать уверенных выводов.
# ── Свежесть лаб-тестов: нейтральная база + условно-гейтированные (нить diagnosis-hardcode B6) ──
# BASE применяется ко всем; условные тесты добавляются по active_conditions
# текущего тенанта. Выдуманный TENANT_DEMO с пустым набором состояний
# получает только BASE, без переноса чужой клинической политики.

# BASE — общие лаб-тесты (CBC, печень, почки, липиды, метаболизм). Релевантны каждому тенанту.
BASE_FRESHNESS = {
    "HGB":          {"days": 90,  "priority": "high"},
    "MCV":          {"days": 90,  "priority": "high"},
    "WBC":          {"days": 90,  "priority": "high"},
    "PLT":          {"days": 90,  "priority": "high"},
    "ALT":          {"days": 90,  "priority": "medium"},
    "AST":          {"days": 90,  "priority": "medium"},
    "GGT":          {"days": 90,  "priority": "medium"},
    "LDH":          {"days": 90,  "priority": "medium"},
    "Amylase":      {"days": 90,  "priority": "medium"},
    "Albumin":      {"days": 120, "priority": "medium"},
    "Creatinine":   {"days": 90,  "priority": "medium"},
    "CRP":          {"days": 90,  "priority": "medium"},
    "Glucose":      {"days": 90,  "priority": "medium"},
    "Ferritin":     {"days": 180, "priority": "medium"},
    "Triglycerides":{"days": 180, "priority": "low"},
    "Cholesterol_Total": {"days": 180, "priority": "low"},
    "LDL":          {"days": 180, "priority": "low"},
    "HDL":          {"days": 180, "priority": "low"},
    "TSH":          {"days": 365, "priority": "low"},
}

# Условно-гейтированные: class_id (clinical_kb) → тесты, релевантные ТОЛЬКО при этом классе.
CONDITION_LABS = {
    # онкомаркеры — бессмысленны без онко-анамнеза, критичны при нём (наблюдение в ремиссии)
    "oncology": {
        "CEA":    {"days": 90, "priority": "critical"},
        "CA19-9": {"days": 90, "priority": "critical"},
        "CA125":  {"days": 90, "priority": "high"},
        # Условный приоритет перекрывает BASE только при активном классе.
        # Выдуманный TENANT_DEMO с пустым набором остаётся на BASE.
        "ALT":    {"days": 90, "priority": "high"},
        "AST":    {"days": 90, "priority": "high"},
        "LDH":    {"days": 90, "priority": "high"},
    },
    # мальабсорбция B12/фолата — при макроцитозе/дефиците
    "b12_folate_deficiency": {
        "Vitamin_B12": {"days": 180, "priority": "high", "missing_note": "не сдавался — нужен при макроцитозе"},
        "Folate":      {"days": 180, "priority": "high", "missing_note": "не сдавался — нужен при макроцитозе"},
    },
    # Классы, заведённые под тенанта, несут свои лабы в своде (clinical_kb condition_attr("labs")).
}


def _kb_labs() -> dict:
    """Лабы классов из свода состояний (clinical_kb, данные тенанта). Нет свода — {}."""
    try:
        import clinical_kb
        return clinical_kb.condition_attr("labs")
    except Exception as e:  # свод нечитаем — условные лабы тенанта не применятся; громко
        log.warning(f"clinical_kb labs недоступны: {e}")
        return {}


def effective_freshness(active_conds) -> dict:
    """Расписание свежести для тенанта: BASE + условные тесты его активных классов.

    active_conds — set condition_id текущего тенанта из clinical_kb.active_conditions.
    Выдуманный TENANT_DEMO с пустым set получает только BASE.
    """
    sched = dict(BASE_FRESHNESS)
    for cond in (active_conds or set()):
        for test, cfg in {**CONDITION_LABS.get(cond, {}), **_kb_labs().get(cond, {})}.items():
            sched[test] = cfg
    return sched


# DATA_FRESHNESS — ПОЛНЫЙ каталог (BASE + все условные) для обратной совместимости/инвентаря.
# НЕ per-tenant источник: живые потребители (_seed_data_freshness, gp_context) переведены на
# effective_freshness(active_conds). Держим как справочник всех известных тестов.
DATA_FRESHNESS = dict(BASE_FRESHNESS)
for _ct in [*CONDITION_LABS.values(), *_kb_labs().values()]:
    DATA_FRESHNESS.update(_ct)


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
