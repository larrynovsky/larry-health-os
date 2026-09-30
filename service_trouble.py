#!/usr/bin/env python3.11
"""service_trouble.py — гипотеза «человеку плохо от СИСТЕМЫ, а не от здоровья».

Независимо придуманный пример: ссылка на учебный отчёт открывает пустую страницу.
Человек сообщает, что отчёт не открывается, а бот объясняет, где искать кнопку,
не передавая жалобу оператору. Человек прекращает попытки. Здоровье процесса
не доказывает, что получатель смог воспользоваться результатом.

Модуль отвечает на ОДИН вопрос: «похоже ли, что этот человек жалуется на работу
системы?» — и возвращает вердикт со ступенью действия. Он НЕ отправляет сообщений
и НЕ пишет в БД: доставка — дело `notify.notify_operator`, запись — дело вызывающего.
Разделение намеренное (D1): судить, доставлять и хранить — три разные обязанности,
и слитые вместе они превращают одну поломку в три.

СУДЬЯ ОТДЕЛЁН ОТ ОТВЕЧАЮЩЕГО (§17). Оценку даёт вызов модели, НЕ тот, что писал
ответ человеку. Проверка, идущая тем же путём, что и гипотеза, не может обнаружить
искажение: успокаивающий ответ не является независимой оценкой жалобы.
`judge` инъектируется, поэтому оракулы гоняются без сети.

ГРАНИЦА, которую машина не проверяет: вердикт судьи — эвристика (RST, Oracle
Heuristic). Ложные тревоги мы увидим; ПРОПУСКИ измерить нечем — видны только
пойманные. Компенсация одна: на средней уверенности бот спрашивает человека, и
ответ человека и есть разметка.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("service_trouble")

# Пороги — политика анализа, а не константа (§9): дом в config_db, здесь резерв.
_FALLBACK = {"service_trouble.ask_at": 0.4, "service_trouble.escalate_at": 0.75}
_BURST_WINDOW_S = 600      # структура окна «подряд», не клиническое число
_BURST_MIN_MSGS = 3


@dataclass
class Verdict:
    trouble: bool
    confidence: float
    why: str
    action: str                      # 'silent' | 'ask' | 'escalate'
    signals: dict = field(default_factory=dict)


def _echoed_system_text(user_text: str, tenant: str, dashboard_host: str) -> bool:
    """Человек прислал боту текст, который система сама и напечатала.

    Самый сильный признак и единственный, не требующий суждения. Оба маркера
    ВЫЧИСЛЯЮТСЯ (§18): префикс — из имени тенанта, хост — из константы дашборда.
    Списка фраз здесь нет намеренно — он протух бы первым же новым уведомлением.
    """
    if not user_text:
        return False
    return f"[{tenant}]" in user_text or (bool(dashboard_host) and dashboard_host in user_text)


def _burst(user_msg_times: list[float], now: float) -> bool:
    """Разрыв ритма: три и больше сообщений за десять минут.

    Сам по себе ничего не значит (человек может увлечённо рассказывать) — только
    повышает уверенность рядом с другими признаками.
    """
    return sum(1 for t in user_msg_times if now - t <= _BURST_WINDOW_S) >= _BURST_MIN_MSGS


def _collect_signals(user_text: str, tenant: str, dashboard_host: str,
                    user_msg_times: list[float], now: float,
                    outgoing_last_hour: int, outgoing_norm: int) -> dict:
    """Чистое ядро: наблюдения без суждения. Ничего не решает."""
    return {
        "_echoed_system_text": _echoed_system_text(user_text, tenant, dashboard_host),
        "_burst": _burst(user_msg_times or [], now),
        "outgoing_anomaly": outgoing_norm > 0 and outgoing_last_hour > outgoing_norm * 10,
    }


def _weigh(signals: dict, judge_says_trouble: bool, judge_confidence: float) -> float:
    """Свести наблюдения и вердикт судьи в одну уверенность.

    Эхо системного текста — самостоятельное основание: человек буквально принёс
    нам нашу же поломку. Оно ставит ПОЛ уверенности, даже если судья не понял.
    Остальные признаки только подталкивают.
    """
    conf = judge_confidence if judge_says_trouble else 0.0
    if signals.get("_echoed_system_text"):
        conf = max(conf, 0.8)
    if signals.get("outgoing_anomaly"):
        conf = min(1.0, conf + 0.15)
    if signals.get("_burst"):
        conf = min(1.0, conf + 0.05)
    return round(conf, 3)


def _decide(conf: float, is_owner: bool, ask_at: float, escalate_at: float) -> str:
    """Лестница §13: сначала дешёвое и обратимое, человек — верхняя ступень.

    Владельцу бот вопросов не задаёт (решение владельца 2026-08-02): он же оператор,
    уточнять у него бессмысленно — ему сразу эскалация или тишина.
    """
    if conf >= escalate_at:
        return "escalate"
    if conf >= ask_at:
        return "escalate" if is_owner else "ask"
    return "silent"


def _thresholds(conn=None) -> dict:
    try:
        import config_db
        return {k: float(config_db.get_config(k, v, conn=conn)) for k, v in _FALLBACK.items()}
    except Exception as e:  # noqa: BLE001 — нет БД: резерв, но ГРОМКО (§14)
        log.warning(f"пороги недоступны в БД ({e}) — взят резерв {_FALLBACK}")
        return dict(_FALLBACK)


def assess(user_text: str, assistant_reply: str, *, tenant: str, dashboard_host: str,
           is_owner: bool, user_msg_times=None, now: float = 0.0,
           outgoing_last_hour: int = 0, outgoing_norm: int = 0,
           judge=None, thresholds=None) -> Verdict:
    """Единственный публичный вход. Возвращает вердикт, ничего не делая с миром.

    `judge(user_text, assistant_reply) -> (bool, float, str)` — отдельный вызов
    модели. None → судья не спрашивается вовсе (уверенность соберётся только из
    наблюдаемых признаков). Отказ судьи НЕ роняет вызывающего и НЕ обнуляет эхо.
    """
    sig = _collect_signals(user_text, tenant, dashboard_host,
                          user_msg_times or [], now, outgoing_last_hour, outgoing_norm)
    says, jconf, why = False, 0.0, ""
    if judge is not None:
        try:
            says, jconf, why = judge(user_text, assistant_reply)
        except Exception as e:  # noqa: BLE001 — судья отказал: признаки остаются
            log.warning(f"судья недоступен: {e}")
    conf = _weigh(sig, says, jconf)
    th = thresholds or _thresholds()
    action = _decide(conf, is_owner, th["service_trouble.ask_at"],
                    th["service_trouble.escalate_at"])
    if not why and sig["_echoed_system_text"]:
        why = "человек прислал боту текст, сгенерированный самой системой"
    return Verdict(trouble=conf > 0, confidence=conf, why=why, action=action, signals=sig)



# ── Память гипотезы: без исхода датчик зеленеет сам с собой ──────────────────
# Домен модуля — ГИПОТЕЗА О СЛУЖБЕ: судить и помнить собственный вердикт.
# Доставка и формулировка текста человеку сюда НЕ входят (D1). Своя таблица, а
# не `hypotheses_*`: там клинический домен, и завести туда «система сломана»
# значило бы повторить §16 наоборот — определить домен строки именем таблицы.

_DDL = """
CREATE TABLE IF NOT EXISTS service_trouble (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT DEFAULT (datetime('now')),
    tenant      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    action      TEXT NOT NULL,
    why         TEXT,
    signals     TEXT,
    outcome     TEXT,          -- NULL пока не разрешена: confirmed | false_alarm
    outcome_at  TEXT
)
"""


def remember(v: "Verdict", tenant: str, conn=None) -> int | None:
    """Записать вынесенный вердикт. Возвращает id или None, если БД недоступна.

    Пишутся ПРИЗНАКИ и уверенность, а не текст человека: сообщение тенанта в
    журнал не попадает — это его переписка, и хранить её здесь незачем.
    """
    import json as _json
    try:
        import health_db as _hdb
        with (conn or _hdb.get_conn()) as c:
            c.execute(_DDL)
            cur = c.execute(
                "INSERT INTO service_trouble (tenant, confidence, action, why, signals) "
                "VALUES (?,?,?,?,?)",
                (tenant, v.confidence, v.action, v.why,
                 _json.dumps(v.signals, ensure_ascii=False)))
            return cur.lastrowid
    except Exception as e:  # noqa: BLE001 — журнал не имеет права уронить бота
        log.warning(f"гипотеза не записана: {e}")
        return None


def resolve_outcome(hypothesis_id: int, outcome: str, conn=None) -> bool:
    """Закрыть гипотезу исходом. Только это делает механизм измеримым.

    `confirmed` — проблема была; `false_alarm` — нет. Без исходов через месяц
    нельзя посчитать долю ложных тревог, и порог не откалибруется никогда.
    """
    if outcome not in ("confirmed", "false_alarm"):
        raise ValueError(f"неизвестный исход {outcome!r}")
    try:
        import health_db as _hdb
        with (conn or _hdb.get_conn()) as c:
            c.execute(_DDL)
            c.execute("UPDATE service_trouble SET outcome=?, outcome_at=datetime('now') "
                      "WHERE id=?", (outcome, hypothesis_id))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"исход гипотезы {hypothesis_id} не записан: {e}")
        return False

if __name__ == "__main__":
    # Самопроверка: эхо системного уведомления требует эскалации без судьи.
    v = assess("[health_partner] Новый анализ распознан: 0000abcd…", "Это служебное сообщение",
               tenant="health_partner", dashboard_host="100.64.0.1", is_owner=False,
               thresholds=dict(_FALLBACK))
    assert v.action == "escalate", v
    print("ok:", v)
