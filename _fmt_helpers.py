"""
_fmt_helpers.py — централизованные форматтеры для NULL-aware вывода.

Адресует UC-I-03 (NULL ≠ 0): паттерн `int((stats.get('avg_deep') or 0) * 60)`
неявно превращает NULL в 0, что в f-string даёт «Deep: 0 мин» при отсутствии
данных — медицински неверное утверждение.

Использование:
    from _fmt_helpers import fmt_min, fmt_or
    f"Deep: {fmt_min(stats.get('avg_deep'))} мин"     # → "42" или "—"
    f"HRV: {fmt_or(hrv, 0)} мс"                        # → "22" или "—"
"""
from __future__ import annotations

from datetime import date
from typing import Any

DASH = "—"


def fmt_count(n: int, kind: str, lang: str | None = None) -> str:
    """Число со склонением; формы слов живут вместе с переводами интерфейса."""
    import i18n
    lang = lang or i18n.lang_of()
    one, few, many = i18n.t(f"count.{kind}", lang).split("|")
    count = abs(n)
    if lang == "en":
        word = one if count == 1 else many
    elif count % 10 == 1 and count % 100 != 11:
        word = one
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{n} {word}"


def fmt_label(value: str, kind: str, lang: str | None = None, *,
              unknown_expected: bool = False) -> str:
    """Название статуса или периода для человека; неизвестный код не показываем.

    Промах закрытого словаря (статус, вердикт, период) — расхождение словаря и данных:
    человек видит «неизвестно», поэтому промах уходит в журнал сбоев (иначе молчит).
    unknown_expected=True — словарь заведомо неполон (названия болезней, свои опросники)."""
    import re
    import i18n
    key = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    try:
        return i18n.t(f"{kind}.{key}", lang)
    except KeyError:
        if key and not unknown_expected:
            import notify
            notify.fault(f"_fmt_helpers.fmt_label: нет подписи {kind}.{key}", person_key=None)
        return i18n.t(f"{kind}.unknown", lang)


def fmt_instrument_name(instrument: dict) -> str:
    """Известные сокращения опросников раскрываем, пользовательское название сохраняем."""
    import i18n
    label = fmt_label(instrument.get("id", ""), "assessment.name", unknown_expected=True)
    if label != i18n.t("assessment.name.unknown"):
        return label
    name = i18n.pick(instrument.get("name"))
    return name if name and name.casefold() != str(instrument.get("id", "")).casefold() else label

# Падежи дня недели: [0]=именительный, [1]=родительный («с субботы»), [2]=винительный («на воскресенье»)
_RU_WEEKDAY_CASES = (
    ("понедельник", "понедельника", "понедельник"),
    ("вторник", "вторника", "вторник"),
    ("среда", "среды", "среду"),
    ("четверг", "четверга", "четверг"),
    ("пятница", "пятницы", "пятницу"),
    ("суббота", "субботы", "субботу"),
    ("воскресенье", "воскресенья", "воскресенье"),
)


def fmt_weekday_ru(d: date) -> str:
    """День недели по-русски, именительный падеж.

    >>> fmt_weekday_ru(date(2026, 9, 6))
    'воскресенье'
    """
    return _RU_WEEKDAY_CASES[d.weekday()][0]


def fmt_night_ru(wake_date: date) -> str:
    """Ночь, закончившаяся утром wake_date: «с субботы на воскресенье». LLM считает день
    недели из ISO-даты ненадёжно (бриф 2026-09-06 назвал ночь «с воскресенья на
    понедельник») — метка отдаётся ему готовой.

    >>> fmt_night_ru(date(2026, 9, 6))
    'с субботы на воскресенье'
    >>> fmt_night_ru(date(2026, 9, 7))
    'с воскресенья на понедельник'
    >>> fmt_night_ru(date(2026, 9, 9))
    'со вторника на среду'
    """
    prev = _RU_WEEKDAY_CASES[(wake_date.weekday() - 1) % 7][1]
    cur = _RU_WEEKDAY_CASES[wake_date.weekday()][2]
    pre = "со" if prev.startswith("вт") else "с"
    return f"{pre} {prev} на {cur}"


def fmt_min(value: float | int | None, default: str = DASH) -> str:
    """
    Часы как float → минуты как str. None → default.

    >>> fmt_min(0.7)
    '42'
    >>> fmt_min(None)
    '—'
    >>> fmt_min(0)
    '0'
    """
    if value is None:
        return default
    return str(int(value * 60))


def fmt_or(value: Any, format_spec: str | int | None = None,
           default: str = DASH) -> str:
    """
    Универсальный: number → форматированная строка, None → default.

    Args:
        value: число или None.
        format_spec:
            - int → знаков после запятой: 0 → "22"; 1 → "22.5".
            - str → формат-спецификатор: ".1f", "06d".
            - None → default str(value).

    >>> fmt_or(22, 0)
    '22'
    >>> fmt_or(22.456, 1)
    '22.5'
    >>> fmt_or(None)
    '—'
    >>> fmt_or(0)
    '0'
    """
    if value is None:
        return default
    if isinstance(format_spec, int):
        return f"{value:.{format_spec}f}" if isinstance(value, float) else str(value)
    if isinstance(format_spec, str):
        return format(value, format_spec)
    return str(value)


def fmt_hours(value: float | None, default: str = DASH) -> str:
    """
    Часы как float → строка с одной десятичной. None → default.

    >>> fmt_hours(7.5)
    '7.5'
    >>> fmt_hours(None)
    '—'
    """
    if value is None:
        return default
    return f"{value:.1f}"
