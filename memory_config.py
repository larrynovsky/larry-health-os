"""
memory_config — персональные лексиконы и пороги памяти в БД (system_config), НЕ в коде.

Правило проекта: персональные справочники (симптомы, red-flag, durable-маркеры) и пороги —
данные владельца, редактируются без коммита/деплоя (как absolute_thresholds/trend_thresholds).
Тонкая обёртка над config_db: имя-конвенция + tuple + код-fallback (сбой БД не рушит читателя).

Ключи в system_config:
  memory_lexicon.<name>  → value_json (список терминов)
  memory_tuning.<name>   → value_num  (число)

Правка: дашборд / прямой upsert. Читатели зовут get_lexicon()/get_param() на каждом вызове
(дёшево; меняется редко) — правка подхватывается сразу, без рестарта.
"""
from __future__ import annotations
# config_db импортируется ЛЕНИВО внутри функций — разрывает цикл импорта
# (memory_salience → memory_config → config_db → … → memory_salience).


def get_lexicon(name: str, default) -> tuple[str, ...]:
    """Список терминов из memory_lexicon.<name>. Нет/пусто/сбой → код-дефолт."""
    try:
        import config_db as _cfg
        v = _cfg.get_config(f"memory_lexicon.{name}")
        if isinstance(v, list) and v:
            return tuple(str(x) for x in v)
    except Exception:
        pass  # silent-ok: fallback на код-дефолт — сбой БД/схемы не должен ронять читателя памяти
    return tuple(default)


def get_param(name: str, default):
    """Число из memory_tuning.<name>. Нет/сбой → код-дефолт (тип сохраняется)."""
    try:
        import config_db as _cfg
        v = _cfg.get_config(f"memory_tuning.{name}")
        if isinstance(v, (int, float)):
            return type(default)(v)
    except Exception:
        pass  # silent-ok: fallback на код-дефолт — сбой БД не должен ронять читателя памяти
    return default


def seed(lexicons: dict, params: dict, overwrite: bool = False) -> dict:
    """Засеять код-дефолты в system_config (idempotent). overwrite=False — не трогать уже
    правленное владельцем. Возвращает {seeded, skipped}."""
    import config_db as _cfg
    seeded = skipped = 0
    for name, terms in lexicons.items():
        key = f"memory_lexicon.{name}"
        if not overwrite and _cfg.get_config(key) is not None:
            skipped += 1
            continue
        _cfg.upsert_config(key, value_json=list(terms), category="memory_lexicon", source="seed")
        seeded += 1
    for name, val in params.items():
        key = f"memory_tuning.{name}"
        if not overwrite and _cfg.get_config(key) is not None:
            skipped += 1
            continue
        _cfg.upsert_config(key, value_num=float(val), category="memory_tuning", source="seed")
        seeded += 1
    return {"seeded": seeded, "skipped": skipped}
