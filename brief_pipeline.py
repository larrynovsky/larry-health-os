"""
brief_pipeline.py — Ф4: сборка карточек из провайдеров GP-контекста.

assemble_cards(target): вызывает существующие провайдеры (genome/drift/safety/hypotheses),
адаптирует их сырые структуры в Card (brief_cards), дедуплицирует. Это вход для
brief_state.advance (gate+slot+FSM). Использует те же провайдеры, что и
gp_agent.generate_daily_report; подключение управляется флагом MORNING_BRIEF_GATE.

Импорты провайдеров — ленивые (внутри функции): чтобы импорт brief_pipeline был дёшев
и не тянул health_db на этапе загрузки. Геном фильтруется по значимости; повторная
карточка с неизменным содержанием требует дедупликации. Провайдер упал →
громкий лог + продолжаем (частичный контекст лучше пустого; датчик, не тихий pass).
"""
from __future__ import annotations

# INTENT: morning_brief
import json
import logging
from datetime import date
from pathlib import Path

from _time_inject import get_today   # контракт единого времени, не date.today()

log = logging.getLogger(__name__)

_GENOME_DOMAINS = ("sleep", "energy", "stress", "movement")
_SIGNIFICANT = ("Pathogenic", "Likely path", "Risk")


PROVIDER_FAILURES_PATH = Path(__file__).parent / "logs" / "brief_provider_failures.json"


def _record_provider_failure(name: str, exc: BaseException) -> None:
    """Отказ провайдера — НАРУЖУ, а не только в лог (2026-08-11).

    Проглатывать отказ правильно: бриф не должен падать целиком из-за одной
    секции. Неправильно было другое — единственным следом оставалась строка
    WARNING в 59-мегабайтном логе бота, которую не читает никто и никогда.
    Замер 11.08: `recovery` провайдер падал с AttributeError, карточка
    восстановления не собиралась, и наружу не выходило НИЧЕГО.

    Пишем факт в маленький артефакт рядом с логами; читатель — ночной датчик
    живости брифа. Своего канала доставки не заводим: у брифа он уже есть.
    Артефакт перезаписывается целиком за сутки — он про «сегодня», а не журнал.
    """
    try:
        PROVIDER_FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(PROVIDER_FAILURES_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        today = get_today().isoformat()
        if data.get("date") != today:
            data = {"date": today, "failures": {}}
        # Тип исключения, НЕ текст: текст может нести значения (§19), тип — нет.
        data["failures"][name] = type(exc).__name__
        tmp = PROVIDER_FAILURES_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        tmp.replace(PROVIDER_FAILURES_PATH)
    except Exception:  # noqa: BLE001
        # Учёт отказа не имеет права уронить бриф — иначе лекарство хуже болезни.
        log.warning("не смог записать отказ провайдера %s", name)


def assemble_cards(target: date, hypotheses_n: int = 5) -> list:
    """Реальные провайдеры за `target` → список Card (дедуплицированный)."""
    import health_db as db
    import genome_context as gc
    import health_ai as hai
    import safety_net as sn
    import brief_cards as bc

    cards: list = []
    sleep_mods: list = []  # sleep-геном СКЛЕЕН с находкой sleep:deep, не отдельная карточка

    # Геном значимых вариантов. sleep-домен склеивается с sleep:deep (один кулдаун),
    # остальные домены — редкий самостоятельный контекст.
    try:
        for domain in _GENOME_DOMAINS:
            genes = gc.LIFESTYLE_GENE_MAP.get(domain, [])
            for v in (db.get_variants_by_genes(genes, limit=30) if genes else []):
                vd = dict(v)
                if not any(w in (vd.get("significance") or "") for w in _SIGNIFICANT):
                    continue
                if domain == "sleep":
                    sleep_mods.append(f"{vd.get('gene')} {vd.get('genotype')} {vd.get('significance')}")
                else:
                    cards.append(bc.from_genome(vd, domain))
    except Exception as e:
        log.warning("assemble_cards genome провайдер упал: %r", e)
        _record_provider_failure("genome", e)

    try:
        for d in (hai.detect_metric_drift(target=target) or []):
            cards.append(bc.from_drift(d))
    except Exception as e:
        log.warning("assemble_cards drift провайдер упал: %r", e)
        _record_provider_failure("drift", e)

    try:
        for a in (sn.run_safety_net(target).get("alerts") or []):
            cards.append(bc.from_safety(a))
    except Exception as e:
        log.warning("assemble_cards safety провайдер упал: %r", e)
        _record_provider_failure("safety", e)

    try:
        for h in (hai.get_open_hypotheses(n=hypotheses_n) or []):
            cards.append(bc.from_hypothesis(h))
    except Exception as e:
        log.warning("assemble_cards hypotheses провайдер упал: %r", e)
        _record_provider_failure("hypotheses", e)

    # Sleep-агент: хронический глубокий ниже ЛИЧНОЙ полосы (band-anchor) → тот же FSM
    try:
        import brief_gate as bg
        with db.get_conn() as c:
            rows = c.execute(
                "SELECT sleep_deep FROM daily_metrics WHERE date<=? AND sleep_deep IS NOT NULL "
                "ORDER BY date DESC LIMIT 60", (str(target),)).fetchall()
        series = [r[0] * 60 for r in rows if r[0]]
        if series and len(series) >= 15:
            today_deep, window = series[0], series[1:]
            avg7 = sum(series[:7]) / min(len(series), 7)
            if avg7 < 60:  # хронически низкий недельный глубокий (норма агента >60м) → находка есть
                band = bg.band_position(window, today_deep)
                cards.append(bc.from_sleep_deep(int(today_deep), band, genome_mods=sleep_mods))
    except Exception as e:
        log.warning("assemble_cards sleep-deep провайдер упал: %r", e)
        _record_provider_failure("sleep-deep", e)

    # Индекс восстановления: композит ниже ЛИЧНОЙ trailing-полосы → карточка.
    # Полоса учитывает базовый уровень ряда. Композит может поймать диффузное
    # проседание без отдельного drift; дедуп — общий pulse-слот (severity-ранг).
    try:
        import brief_gate as bg
        series = hai.recovery_series(target, days=60)
        if series and len(series) >= 15:
            today_comp, window = series[0], series[1:]
            band = bg.band_position(window, today_comp)
            card = bc.from_recovery(today_comp, band)
            if card:
                cards.append(card)
    except Exception as e:
        log.warning("assemble_cards recovery провайдер упал: %r", e)
        _record_provider_failure("recovery", e)

    # Среда (Open-Meteo/aqicn) — жара/UV/пыль/воздух
    try:
        import env_context as _ec
        cards += _ec.assemble_env_cards(target)
    except Exception as e:
        log.warning("assemble_cards env провайдер упал: %r", e)
        _record_provider_failure("env", e)

    # Календарь (второй этап): БЛИЖАЙШАЯ поездка «за день до» — одна карта, не шум.
    try:
        import calendar_client as _cal
        _imm = [e for e in _cal.get_travel_events(days=3)
                if (e.get("date_raw") or "").startswith(("today", "tomorrow", "day after"))]
        # предпочитаем событие С локацией (информативнее), иначе первое ближайшее
        _pick = next((e for e in _imm if (e.get("location") or "").strip()), _imm[0] if _imm else None)
        if _pick is not None:
            cards.append(bc.from_calendar(_pick))  # только ближайшая, не весь календарь
    except Exception as e:
        log.warning("assemble_cards calendar провайдер упал: %r", e)
        _record_provider_failure("calendar", e)

    # Сезон×геном (второй этап): полезный сезонный продукт ПЕР-ТЕНАНТ (геном ЭТОЙ БД).
    # Продукт ДНЯ — ротация по дате (фрукты→овощи→морепродукты), день за днём разный.
    try:
        import food_profile as _fp
        # Продукт ДНЯ: сезонное + несезонные staples, РАМКА-осознанно (медкарта доминирует —
        # напр. energy=gain → калорийные пометки, не «лёгкое»). Ротация по дате.
        _it = _fp.pick_food_of_day(month=target.month, day_index=target.toordinal())
        if _it:
            cards.append(bc.from_season_food(_it))
    except Exception as e:
        log.warning("assemble_cards food провайдер упал: %r", e)
        _record_provider_failure("food", e)

    # Трейлы (второй этап, семейная активность): выходной + тенант дома + погода не опасна.
    # Карта каждому, когда ОН дома (решение 2026-07-14). Кулдаун per-тропа ротирует список.
    try:
        if target.weekday() in (5, 6):                 # суббота/воскресенье
            import trails as _tr, location_signal as _ls2
            _pl2 = _ls2.resolve_place()
            _hazard = any(getattr(c, "provider", "") == "env" and getattr(c, "lane", "") == "safety"
                          for c in cards)               # жара/пыль/шторм → не зовём в поход
            if _pl2.get("known") and _pl2.get("is_home") and not _hazard:
                _t = _tr.pick_trail(target.toordinal())
                if _t:
                    cards.append(bc.from_trail(_t))
    except Exception as e:
        log.warning("assemble_cards trail провайдер упал: %r", e)
        _record_provider_failure("trail", e)

    return bc.dedup(cards)


def gate_sleep_brief(brief: str, show_chronic: bool) -> str:
    """show_chronic=False (хронический глубокий на кулдауне) → убрать недельную рамку
    глубокого (deep в строках 7d/30d avg + флаг «мало глубокого»), оставив сегодняшнюю
    ночь. Гасит «устойчивую недельную картину» до раза в месяц."""
    import re
    if show_chronic or not brief:
        return brief
    out = []
    for ln in brief.split("\n"):
        if ln.lstrip().startswith(("7d avg", "30d avg")):
            continue  # убрать недельную рамку ЦЕЛИКОМ — иначе LLM editorialize'ит «устойчивую картину»
        if "мало глубокого" in ln:
            t = re.sub(r";?\s*мало глубокого сна \([^)]*\)", "", ln).replace("⚠ ; ", "⚠ ").rstrip()
            if t.strip() in ("⚠", ""):
                continue
            out.append(t)
            continue
        out.append(ln)
    return "\n".join(out)
