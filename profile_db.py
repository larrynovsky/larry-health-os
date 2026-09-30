"""profile_db.py — доменный модуль profile. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def get_patient_profile(category: str = None) -> dict:
    """Возвращает профиль пациента как словарь key→value.
    Если value_json — возвращает распарсенный объект, иначе value_text."""
    import json as _json
    with _hdb.get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT key, value_text, value_json FROM patient_profile WHERE category=?",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, value_text, value_json FROM patient_profile"
            ).fetchall()
    result = {}
    for row in rows:
        if row["value_json"]:
            try:
                result[row["key"]] = _json.loads(row["value_json"])
            except Exception:
                result[row["key"]] = row["value_text"]
        else:
            result[row["key"]] = row["value_text"]
    return result


_FIELDS_PATH = Path(__file__).resolve().parent / "methodology" / "profile_fields.yaml"


def profile_fields() -> dict:
    """Поля профиля, которые человек сообщает о себе сам: key → спецификация.
    Один дом — methodology/profile_fields.yaml (его же читает знакомство в боте)."""
    import yaml
    return (yaml.safe_load(_FIELDS_PATH.read_text(encoding="utf-8")) or {}).get("fields") or {}


def _field_for(name: str, fields: dict) -> str | None:
    n = str(name).strip().lower()
    if n in fields:
        return n
    for key, spec in fields.items():
        if n in [str(a).lower() for a in spec.get("aliases") or []]:
            return key
    return None


def _normalize(spec: dict, raw) -> str | None:
    """Сырой ответ человека → значение поля или None (не разобрано / вне допуска)."""
    import re
    s = str(raw).strip()
    if not s:
        return None
    kind = spec.get("type")
    if kind == "text":
        return s if len(s) <= int(spec.get("max_len", 200)) else None
    if kind == "number":
        m = re.search(r"-?\d+(?:[.,]\d+)?", s)
        if not m:
            return None
        v = float(m.group().replace(",", "."))
        if not (float(spec["min"]) <= v <= float(spec["max"])):
            return None
        return str(int(v)) if v == int(v) else str(v)
    if kind == "date":
        from datetime import date as _date, datetime as _dtm
        d = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                d = _dtm.strptime(s[:10], fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            return None
        return d.isoformat() if d <= get_today() else None
    if kind in ("enum", "bool"):
        low = s.lower()
        syn = {str(k).lower(): str(v).lower() for k, v in (spec.get("synonyms") or {}).items()}
        allowed = [str(v).lower() for v in (spec.get("values") or ["true", "false"])]
        v = syn.get(low, low)
        return v if v in allowed else None
    return None


def apply_stated(name: str, raw, source: str) -> tuple[str, str] | None:
    """Записать в профиль то, что человек сказал о себе, — если у сказанного есть поле.

    До 2026-09-23 здесь был update_profile_field: он писал в profile_context.json, а профиль
    читается из patient_profile (JSON — лишь запасной путь при ПУСТОЙ таблице). Замер: у двух
    тенантов 242 сказанных факта, ни один не дошёл до профиля (нить profile-home).
    Возвращает (ключ, значение) записанного либо None: поля нет или ответ не прошёл проверку —
    тогда сказанное остаётся только в памяти, как и раньше."""
    fields = profile_fields()
    key = _field_for(name, fields)
    if key is None:
        return None
    value = _normalize(fields[key], raw)
    if value is None:
        log.info("apply_stated: %s=%r не прошёл проверку поля %s — в профиль не пишу", name, raw, key)
        return None
    upsert_profile(key, value_text=value, category=key.split(".", 1)[0], updated_by=source)
    return key, value


def upsert_profile(key: str, value_text=None, value_json=None,
                   category: str = None, updated_by: str = "manual") -> None:
    """Записывает или обновляет поле профиля пациента."""
    import json as _json
    if isinstance(value_json, (dict, list)):
        value_json = _json.dumps(value_json, ensure_ascii=False)
    # Раздел — префикс ключа, если не назван: иначе ON CONFLICT затирал его NULL-ом,
    # и строка уходила на странице профиля в «(no category)» (6 строк владельца, 26.09).
    category = category or (key.split(".", 1)[0] if "." in key else None)
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO patient_profile (key, value_text, value_json, category, updated_at, updated_by)
               VALUES (?, ?, ?, ?, datetime('now'), ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_text = excluded.value_text,
                   value_json = excluded.value_json,
                   category   = excluded.category,
                   updated_at = excluded.updated_at,
                   updated_by = excluded.updated_by""",
            (key, value_text, value_json, category, updated_by),
        )


def _overlay_medkarta(nested: dict) -> None:
    """Медицинские поля, у которых есть дом в медкарте, читаются оттуда — для ВСЕХ
    потребителей профиля разом (26.09, владелец: «медицина в профиле бессмысленна»).

    - treatment_status: из эпизодов лечения; ручная строка — только если эпизодов нет.
    - last_pet_ct_result: результат вписан руками, дата исследования приходит из документов.
      Если результат внесён РАНЬШЕ последнего исследования, он о прошлом снимке — так и
      говорится, иначе модель прочтёт старый результат как итог нового.
    """
    med = nested.setdefault("medical", {})
    try:
        import treatment_summary as _ts
        st = _ts.treatment_status_text()
        if st:
            med["treatment_status"] = st
    except Exception as e:  # silent-ok: без медкарты остаётся ручная строка, если есть
        log.warning(f"_overlay_medkarta: статус лечения не выведен: {e}")
    res, pet = med.get("last_pet_ct_result"), str(med.get("last_pet_ct") or "")[:10]
    if res and pet:
        with _hdb.get_conn() as conn:
            row = conn.execute("SELECT updated_at FROM patient_profile "
                               "WHERE key='medical.last_pet_ct_result'").fetchone()
        entered = (row[0] or "")[:10] if row else ""
        if entered and entered < pet:
            med["last_pet_ct_result"] = (f"{res} [внесён {entered}, до исследования {pet}: "
                                         "результат последнего исследования не внесён]")


def get_profile_context() -> dict:
    """Читает профиль пациента. DB-first (patient_profile), fallback на JSON.
    Возвращает nested dict: {identity: {...}, medical: {...}, ...}"""
    # ── DB path ────────────────────────────────────────────────────────
    flat = _hdb.get_patient_profile()
    if flat:
        nested: dict = {}
        for key, val in flat.items():
            if "." in key:
                category, field = key.split(".", 1)
                nested.setdefault(category, {})[field] = val
            else:
                nested[key] = val
        _overlay_medkarta(nested)
        return nested
    # ── Fallback: JSON file ─────────────────────────────────────────────
    import json as _json
    profile_path = _hdb._HEALTH_DIR / "data" / "profile_context.json"
    if not profile_path.exists():
        return {}
    return _json.loads(profile_path.read_text())


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
