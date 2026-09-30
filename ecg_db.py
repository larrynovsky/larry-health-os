"""ecg_db.py — доменный модуль ЭКГ (Apple Watch через Health Auto Export).

Хранит ВЕРДИКТ + метаданные (classification/avg_hr/severity/sampling), НЕ сырую
волну (512 Гц × 30с = 15360 точек на запись — тяжело и бесполезно в БД). Полный
payload с волной остаётся в провенанс-файле ecg_rest/ (см. api_hae_ingest).

Дедуп по (start_time, source): одна запись ЭКГ уникально определяется меткой старта.
Читатели: integrity_tests.check_ecg_nonsinus (кардио-алерт) + gp_context (контекст врача).
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import json
import logging
from datetime import datetime, timezone

import health_db as _hdb

log = logging.getLogger(__name__)

# Классификации Apple, которые НЕ являются нормальным синусовым ритмом и требуют внимания.
# «Inconclusive*» — не патология, а плохой сигнал/пульс вне диапазона → не алертим (шум).
NONSINUS_ALERT = {"Atrial Fibrillation", "High Heart Rate"}


def _parse_start(raw: str) -> str | None:
    """HAE-формат 'yyyy-MM-dd HH:mm:ss Z' → ISO-UTC для сортировки/окон. None если не распарсилось."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S %z")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def save_ecg_readings(entries: list[dict], source_file: str = "") -> int:
    """Пишет записи ЭКГ (без волны). Дедуп по (start_time, source). Возвращает число вставленных.

    Единственная точка записи ecg_readings. entries — массив HAE `data.ecg[]`.
    """
    inserted = 0
    with _hdb.get_conn() as conn:
        for e in entries:
            start_iso = _parse_start(e.get("start", ""))
            if not start_iso:
                log.warning("ecg: пропуск записи без валидного start: %r", e.get("start"))
                continue
            meta = {k: v for k, v in e.items() if k != "voltageMeasurements"}
            cur = conn.execute("""
                INSERT INTO ecg_readings
                    (start_time, end_time, date, classification, severity,
                     avg_hr, n_voltage, sampling_hz, source, source_file, raw_meta)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(start_time, source) DO NOTHING
            """, (
                start_iso,
                _parse_start(e.get("end", "")),
                start_iso[:10],
                e.get("classification") or "Unrecognized",
                e.get("severity"),
                e.get("averageHeartRate"),
                e.get("numberOfVoltageMeasurements"),
                e.get("samplingFrequency"),
                e.get("source") or "Apple Watch",
                source_file,
                json.dumps(meta, ensure_ascii=False),
            ))
            if cur.rowcount:
                inserted += 1
    return inserted


def find_nonsinus(hours: int = 48) -> list[dict]:
    """Записи ЭКГ с тревожной классификацией (AFib/High HR) за последние N часов.

    Читатель кардио-алерта. Окно 48ч → на суточном прогоне integrity алерт сработает
    ~1-2 раза на новую запись и затихнет (ЭКГ снимается вручную, редко)."""
    from datetime import timedelta
    cutoff = (get_now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    placeholders = ",".join("?" * len(NONSINUS_ALERT))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"SELECT start_time, classification, avg_hr, source "
            f"FROM ecg_readings "
            f"WHERE classification IN ({placeholders}) AND start_time >= ? "
            f"ORDER BY start_time DESC",
            (*sorted(NONSINUS_ALERT), cutoff)
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_ecg(limit: int = 5) -> list[dict]:
    """Последние N записей ЭКГ (для контекста врача). Все классификации."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT start_time, classification, avg_hr, severity, source "
            "FROM ecg_readings ORDER BY start_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
