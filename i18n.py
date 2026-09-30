"""i18n — строки интерфейса на языке человека (нить i18n, 28.09).

Один домен: «ключ строки интерфейса + язык человека → текст». Строки — данные (§9), живут в
methodology/i18n/<язык>.yaml; язык — поле профиля identity.language, которое человек выбирает
первым вопросом знакомства. Нет поля — русский: владелец и партнёр знакомство на английском не
проходили, и дефолт обязан оставить их там, где они есть.

Граница: только тексты, которые видит человек (кнопки, ответы бота, вопросы знакомства). Не
pромпты моделей (исключение — model.answer_language: язык ответа модели, см. hai_core.answer_language), не медицинские словари сторожей, не внутренние значения базы. Тексты задач, которые читает человек, входят в этот слой.

Нет перевода ключа — русский текст и предупреждение в журнал. Главный сторож пропусков —
не рантайм, а коммит: tests/unit/test_i18n.py требует одинаковых ключей в ru и en и
двуязычного опросника знакомства, так что пропуск не доезжает до человека. Нет ключа и в
русском — KeyError: опечатка в имени ключа должна падать, а не печатать пустоту.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

LANGS = ("ru", "en")
DEFAULT = "ru"
FIELD = "identity.language"
_DIR = Path(__file__).resolve().parent / "methodology" / "i18n"


@lru_cache(maxsize=None)
def _table(lang: str) -> dict:
    import yaml
    p = _DIR / f"{lang}.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def lang_of(profile: dict | None = None) -> str:
    """Язык человека из профиля; пусто или неизвестное значение — русский."""
    if profile is None:
        try:
            import profile_db
            profile = profile_db.get_patient_profile()
        except Exception as e:  # silent-ok: профиль недоступен — язык по умолчанию, причина в логе
            log.warning("i18n: профиль не прочитан (%s) — язык по умолчанию", type(e).__name__)
            profile = {}
    v = (profile or {}).get(FIELD)
    return v if v in LANGS else DEFAULT


def t(key: str, lang: str | None = None, **kw) -> str:
    """Текст строки `key` на языке `lang` (None — язык человека из профиля)."""
    lang = lang if lang in LANGS else lang_of()
    text = _table(lang).get(key)
    if text is None:
        text = _table(DEFAULT).get(key)
        if text is None:
            raise KeyError(f"i18n: ключа {key!r} нет ни в {lang}, ни в {DEFAULT}")
        if lang != DEFAULT:
            log.warning("i18n: нет перевода %s для %s — показан русский текст", key, lang)
    return text.format(**kw) if kw else text


def pick(value, lang: str | None = None) -> str:
    """Текст данных, записанный по языкам ({"ru": …, "en": …}), или строка как есть."""
    if isinstance(value, dict):
        lang = lang if lang in LANGS else lang_of()
        return value.get(lang) or value.get(DEFAULT) or ""
    return "" if value is None else str(value)

