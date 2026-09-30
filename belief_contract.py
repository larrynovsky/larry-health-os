#!/usr/bin/env python3.11
"""belief_contract.py — годен ли продольный анализ для подачи ИИ.

Единственное место, где живёт правило приёмки веры. До 2026-07-26 правила не было: при
отказе статистического гейта `longitudinal_analysis` публиковал СЫРЫЕ корреляции с флажком
`gate_applied=False`, а оба ИИ-читателя (конституции и gp_context) флажок не смотрели —
нефильтрованная связь входила в веру и становилась основой уверенного рассказа
(внешний аудит validation_gate 2026-07-26, находка P1-01; воспроизведена на Studio).

Публичная функция одна — `read_belief()`. Она отдаёт и вердикт приёмки, и возраст веры, и
факт упавшего последнего прогона: читателю нужно всё это одним куском, иначе он соберёт
правило приёмки у себя, и правил станет два (split-brain).

Отказ гейта НЕ стирает прошлую веру (решение владельца 2026-07-26): она остаётся, но несёт
свой возраст и явную отметку «свежий прогон упал». Молчаливая устаревшая вера — тот же класс
ошибки, что молчаливый пропуск: отсутствие события неотличимо от нормы.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from _time_inject import get_today

# Версия схемы веры. 2 = вера публикуется ТОЛЬКО при применённом гейте; у строк версии 1
# (до 2026-07-26) поля schema_version нет — они законны, если gate_applied=True.
BELIEF_SCHEMA_VERSION = 2


def artifact_path(name: str) -> Path:
    """Артефакт гейта ЭТОГО человека: у владельца (и клона) — прежний путь в репо, у другого
    человека — под его каталогом данных (28.09, этап Б заведения человека). Один дом для
    писателя (longitudinal) и читателей (integrity, ИИ-читатели) — лёгкий модуль, без pandas."""
    from secrets_paths import is_owner_data

    if is_owner_data():
        return Path(__file__).parent / name
    from health_db import _resolve_health_dir
    return Path(_resolve_health_dir()) / name


# Квитанция ПРОГОНА гейта (не веры): пишется каждым прогоном, и успешным, и упавшим.
# Кросс-процессная: пишет longitudinal ночью, читают ИИ-читатели и integrity 07:50.
GATE_RUN_RECEIPT = artifact_path("logs/gate_run_receipt.json")
# Артефакты еженедельного longitudinal, которые судит integrity: пишет longitudinal_analysis.
MC_GAP_ARTIFACT = artifact_path("logs/gate_mc_gap_latest.json")
PASSSET_ARTIFACT = artifact_path("logs/gate_passset_history.json")


def _verdict(gate: "dict | None", schema_version=None) -> "tuple[bool, str]":
    """Правило приёмки. Отдельная функция ради позитивного и негативного контроля в тестах."""
    # Версия схемы: отсутствует → строка v1 (до 2026-07-26), законна. Больше известной →
    # ОТКАЗ: будущая форма может значить не то, что читатель думает. До ревью R3 (VG-R3-05)
    # `schema_version` писался с комментарием «читатель отказывает неизвестной схеме», а
    # читатель его вовсе не смотрел — обещание без механизма.
    if schema_version is not None:
        try:
            if int(schema_version) > BELIEF_SCHEMA_VERSION:
                return False, f"schema_too_new={schema_version}"
        except (TypeError, ValueError):
            return False, f"schema_unreadable={schema_version!r}"
    if not isinstance(gate, dict) or not gate:
        return False, "no_gate"
    if gate.get("gate_applied") is not True:
        return False, "gate_not_applied"
    # `status` появился вместе со schema_version=2. Строки старше его не имеют — это законная
    # вера, записанная при УСПЕШНОМ гейте (иначе gate_applied был бы False). Поэтому default
    # "applied": он НЕ ослабляет правило, а признаёт уже лежащие в БД честные строки.
    if gate.get("status", "applied") != "applied":
        return False, f"status={gate.get('status')}"
    return True, "ok"


def read_belief(conn=None, *, receipt_path: "Path | None" = None) -> dict:
    """Последняя вера продольного анализа + вердикт приёмки + состояние последнего прогона.

    conn: открытое соединение (для тестов на фикстуре); None → канонический health_db.
    Возвращает dict:
      accepted      — можно ли подавать ИИ (False = читатель обязан НЕ рендерить корреляции);
      reason        — почему отказано (no_report / unreadable / no_gate / gate_not_applied / …);
      data          — само саммари (None при отказе);
      generated_at  — когда вера построена (из саммари, иначе дата строки);
      age_days      — возраст веры в днях (None, если дату не разобрать);
      run_failed    — последний ПРОГОН гейта упал (вера при этом может быть годной, но старой);
      failed_at, error — из квитанции прогона, для честной формулировки читателю;
      run_id        — идентичность прогона, записавшего эту веру (None у строк до 2026-07-26);
      data_changed_at — момент последнего изменения ДАННЫХ под верой (журнал репарации,
                      локальное время); None = сведений нет, а не «изменений не было»;
      stale_vs_data — вера построена ДО последнего изменения данных. ТРИ состояния:
                      True (устарела), False (проверено, свежая), None (не проверено).
                      Булев флаг здесь был бы враньём: «нет сведений» слилось бы с «всё
                      хорошо» — ровно тот класс, против которого построена вся эта нить;
      receipt_run_id — идентичность прогона из квитанции. Совпадение run_id ⇔ квитанция и
                      вера про ОДИН прогон. Раньше сверка шла по дате, а дата не идентичность:
                      два прогона в сутки неотличимы, и квитанция одного подтверждала веру
                      другого (VG-R4-03). `run_id` отдаётся ВНЕ зависимости от `accepted` —
                      отклонённая вера тоже кем-то записана, и это надо уметь увидеть.
    """
    out = {"accepted": False, "reason": "no_report", "data": None, "generated_at": None,
           "age_days": None, "run_failed": False, "failed_at": None, "error": None,
           "run_id": None, "receipt_run_id": None,
           "data_changed_at": None, "stale_vs_data": None}

    _r = _read_receipt(receipt_path or GATE_RUN_RECEIPT)
    out["receipt_run_id"] = _r.get("run_id")
    if _r.get("status") == "failed":
        out.update(run_failed=True, failed_at=_r.get("at"), error=_r.get("error"))
    elif _r.get("status") == "started":
        # Конверт открыт и не закрыт: прогон стартовал и не вернулся (SIGKILL/OOM/питание).
        # Раньше на диске в этом случае оставалась прошлая `applied` — «всё в порядке»
        # там, где прогона фактически не было (VG-R4-03).
        out.update(run_failed=True, failed_at=_r.get("at"),
                   error="прогон начался и не закрыл квитанцию (убит на полпути?)")

    row = _latest_row(conn)
    if not row:
        return out
    findings, row_date = row
    try:
        data = json.loads(findings)
    except (ValueError, TypeError):
        out["reason"] = "unreadable"
        return out

    out["run_id"] = ((data.get("gate") or {}).get("run_id")
                     if isinstance(data.get("gate"), dict) else None)
    ok, reason = _verdict(data.get("gate"), data.get("schema_version"))
    out["reason"] = reason
    _raw_generated = str(data.get("generated_at") or row_date or "")
    generated = _raw_generated[:10]
    out["generated_at"] = generated or None
    out["age_days"] = _age_days(generated)
    _changed = _last_data_change(conn)
    out["data_changed_at"] = _changed or None
    out["stale_vs_data"] = _stale_vs_data(_raw_generated, _changed)
    if ok:
        out["accepted"] = True
        out["data"] = data
    return out


_LAST_CHANGE_SQL = (
    "SELECT max(datetime(ts, 'localtime')) FROM ("
    "  SELECT repaired_at AS ts FROM data_repair_log"
    "  UNION ALL"
    "  SELECT reverted_at FROM data_repair_log WHERE reverted_at IS NOT NULL)"
)


def _last_data_change(conn):
    """Момент последнего изменения ДАННЫХ под верой — по журналу репарации.

    ДВУСТОРОННИЙ: `max(repaired_at ∪ reverted_at)`, а не «есть незакаченные записи новее
    веры». Односторонний предикат пропускает последовательность репарация → вера → откат:
    живых записей не остаётся, а вера посчитана на данных, которых больше нет. Замер
    2026-07-31: в журнале уже лежат четыре прогона, два из них откаченные целиком —
    это не гипотетический сценарий, а вчерашний день.

    ЧАСОВОЙ ПОЯС — здесь и только здесь. `repaired_at`/`reverted_at` пишутся дефолтом
    SQLite `datetime('now')`, то есть в UTC; `generated_at` веры — локальное время
    (`get_now()`). Замер 2026-07-31 на Studio: расхождение ровно 3 часа, и сравнение
    сырых строк ошибается в сторону ЛОЖНО-ЗЕЛЁНОГО — правка, сделанная через час ПОСЛЕ
    веры, выглядит на три часа старше её. Приведение живёт в одном месте, потому что
    второй его дом означал бы два ответа на вопрос «когда менялись данные».

    ТРИ исхода, а не два: метка времени · `""` = журнал прочитан, изменений в нём нет ·
    None = прочитать не удалось. Слить два последних в один None значило бы объявить
    «датчик ослеп» и «всё чисто» одним и тем же ответом.
    """
    try:
        if conn is not None:
            return conn.execute(_LAST_CHANGE_SQL).fetchone()[0] or ""
        import health_db as db
        with db.get_conn() as c:
            return c.execute(_LAST_CHANGE_SQL).fetchone()[0] or ""
    except Exception as exc:  # silent-ok: «не проверено» доезжает до читателя как None
        if "no such table" not in str(exc):
            print(f"⚠️ belief_contract: журнал репарации не прочитан "
                  f"({type(exc).__name__}: {exc}) — устаревание веры не проверено")
        return None


def _stale_vs_data(generated: str, changed: "str | None") -> "bool | None":
    """Построена ли вера ДО последнего изменения данных. Три состояния, не два.

    У веры старого формата в `generated_at` может лежать только дата. Тогда сравнение
    идёт по дню, и день-в-день считается УСТАРЕВШЕЙ: направление ошибки выбрано в
    сторону лишней перепроверки, потому что цена обратной — число о здоровье,
    посчитанное на данных, которых больше нет.
    """
    if changed is None or not generated:
        return None
    if not changed:
        return False          # журнал прочитан и пуст — данные под верой не трогали
    if len(generated) <= 10:
        return changed[:10] >= generated[:10]
    return changed > generated


def _latest_row(conn):
    """Последняя строка продольного анализа. Тотальный порядок (read-your-writes):
    id DESC разрешает ничью по дате, иначе строка выбирается произвольно."""
    sql = ("SELECT findings, date FROM agent_reports "
           "WHERE agent_type = 'longitudinal_analysis' "
           "ORDER BY date DESC, id DESC LIMIT 1")
    if conn is not None:
        r = conn.execute(sql).fetchone()
        return (r["findings"], r["date"]) if r and r["findings"] else None
    import health_db as db
    try:
        with db.get_conn() as c:
            r = c.execute(sql).fetchone()
    except Exception as exc:
        # Не тихо: «БД недоступна» и «веры нет» — разные события, и читатель обязан
        # различать их хотя бы в выводе прогона (класс «исключение → тишина»).
        print(f"⚠️ belief_contract: вера не прочитана ({type(exc).__name__}: {exc})")
        return None
    return (r["findings"], r["date"]) if r and r["findings"] else None


def _read_receipt(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Квитанции нет или она битая. Это НЕ «прогон упал» — это отсутствие сведений;
        # ловит его отдельный датчик liveness, а не читатель веры.
        return {}


def _age_days(generated: str) -> "int | None":
    try:
        return (get_today() - date.fromisoformat(generated)).days
    except (ValueError, TypeError):
        return None
