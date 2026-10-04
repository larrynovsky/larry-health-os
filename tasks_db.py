"""tasks_db.py — доменный модуль tasks. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import logging


log = logging.getLogger(__name__)


def save_task(source: str, type_: str, content: str,
              priority: str = "medium", deadline: str = None,
              source_date: str = None, reason: str = None,
              fingerprint: str = None,
              resolution_type: str = "self_managed",
              judge_verdict: str = None, judge_reason: str = None) -> int | None:
    """
    Сохраняет задачу в базу.
    Если fingerprint задан и открытая задача с таким fingerprint уже есть — пропускает.
    Возвращает id новой задачи или None если пропущена как дубликат.

    ДВА СЛОЯ, И ИХ НЕЛЬЗЯ ПУТАТЬ (15.09, BL-JUDGE-VERDICT-UNRECORDED-1):
      `reason`        — ПУБЛИКАЦИЯ: рендерится человеку в Telegram курсивом
                        (`task_agent.format_tasks_message`).
      `judge_verdict` / `judge_reason` — ДАННЫЕ: чем машина обосновала себе, что
                        этот вопрос вообще стоит задавать. Человеку не показывается.
    Замер, ради которого разведено: у всех 23 вопросов из памяти в `reason` лежала
    одна константа, а собственная причина судьи — была в руках и терялась.

    Совет сдать анализ получает дату последней сдачи (gp_context.annotate_lab_recency, 27.09):
    человек читает задачу, а не отчёт, и дата нужна ему здесь.
    """
    import gp_context as _gc  # лениво: gp_context импортирует health_db, а тот — этот модуль
    content = _gc.annotate_lab_recency(content)
    with _hdb.get_conn() as conn:
        # Дедупликация по fingerprint
        if fingerprint:
            existing = conn.execute(
                "SELECT id FROM tasks WHERE fingerprint=? AND status='open'",
                (fingerprint,)
            ).fetchone()
            if existing:
                log.info(f"Task skipped (duplicate fingerprint '{fingerprint}'): {content[:50]}")
                return None

        # Поля суждения дописываем ТОЛЬКО когда судья был. Жёсткий INSERT со
        # всеми колонками сразу — первая редакция этой правки — ронял всех, у
        # кого схема без них: три соседних теста и `assessment_scheduler` в бою
        # («table tasks has no column named judge_verdict»). Задача без судьи —
        # норма, а не особый случай: вердикт выносят только вопросам.
        поля = ["source", "source_date", "type", "priority", "content",
                "reason", "fingerprint", "deadline", "resolution_type"]
        значения = [source, source_date, type_, priority, content,
                    reason, fingerprint, deadline, resolution_type]
        for имя, значение in (("judge_verdict", judge_verdict),
                              ("judge_reason", judge_reason)):
            if значение is not None:
                поля.append(имя)
                значения.append(значение)
        cur = conn.execute(
            f"INSERT INTO tasks ({', '.join(поля)}) "
            f"VALUES ({', '.join('?' * len(поля))})", значения)
        return cur.lastrowid


def resolve_task(task_id: int, resolved_text: str = None,
                 status: str = "completed") -> bool:
    """Закрывает ОТКРЫТУЮ (или отложенную) задачу. True — закрыта; False — такой нет
    или она уже закрыта.

    До 28.09 возвращал True всегда и перезаписывал уже закрытую задачу: «/dismiss 999»
    отвечал «отклонена» на несуществующий номер, повторное нажатие кнопки меняло
    итог задачи (холодное чтение сообщений 28.09)."""
    with _hdb.get_conn() as conn:
        cur = conn.execute("""
            UPDATE tasks SET status=?, resolved_at=datetime('now'), resolved_text=?
            WHERE id=? AND status IN ('open', 'snoozed')
        """, (status, resolved_text, task_id))
    return cur.rowcount > 0


def get_open_tasks(limit: int = 20) -> list:
    """Возвращает незакрытые задачи, отсортированные по приоритету."""
    priority_order = "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END"
    with _hdb.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT * FROM tasks
            WHERE status='open'
            ORDER BY {priority_order}, created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_overdue_tasks(days_old: int = 7) -> list:
    """Задачи которые открыты дольше N дней без ответа."""
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM tasks
            WHERE status='open'
              AND julianday('now') - julianday(created_at) > ?
            ORDER BY priority DESC, created_at ASC
        """, (days_old,)).fetchall()
    return [dict(r) for r in rows]


def get_unsent_tasks(type_: str) -> list:
    """Outbox задач одного типа: открытые, с sent_at IS NULL, старшие первыми.

    Обобщение get_unsent_assessment_tasks (2026-09-12, нить question-answer-channel):
    у вопросов та же болезнь, что была у опросников — производитель (GP-агент) и
    отправитель (бот) живут в разных процессах, и без метки отправки задача либо
    не доставляется вовсе, либо доставляется повторно. Метка ставится ПО ID, не по
    MAX(created_at): отказ отправки одной задачи не двигает границу остальным.
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='open' AND type=? "
            "AND sent_at IS NULL ORDER BY created_at, id",
            (type_,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_questions_needing_delivery() -> list:
    """Открытые вопросы, на которые НЕКУДА ответить: нет обратного адреса.

    Признак — `tg_message_id IS NULL`, а не `sent_at IS NULL`. Разница не
    косметическая: вопросы, созданные до 2026-09-12, помечены отправленными
    (их «доставляли» ремайндером), и по критерию sent_at они навсегда остались
    бы немыми — задача открыта, человек её видит, ответить не может. Замер в
    день переключения: у владельца так висел #192, у партнёра — все шесть.

    sent_at остаётся квитанцией ВРЕМЕНИ последней отправки (по ней считает
    дроссель), tg_message_id — квитанцией АДРЕСА. Путать их значит считать
    доставленным то, что доехало другим каналом."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='open' AND type='question' "
            "AND tg_message_id IS NULL ORDER BY created_at, id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_questions() -> list:
    """ВСЕ открытые вопросы, доставленные и нет — множество, против которого
    проверяется дубль (нить question-tails, 2026-09-12).

    Отдельно от `get_questions_needing_delivery`: тот отвечает на вопрос доставки
    («кому некуда ответить»), этот — на вопрос повтора («о чём уже спрошено»).
    Свести их в одну функцию значило бы, что доставленный вопрос перестал быть
    поводом не задавать второй такой же — а он ровно поэтому и задан."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='open' AND type='question' "
            "ORDER BY created_at, id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_tasks_known_to_person(dismissed_window_days: int) -> list:
    """Задачи, которые человек уже держит в голове: открытые, отложенные и СНЯТЫЕ им
    за последние `dismissed_window_days` дней. Против этого множества судья дублей
    сверяет новую задачу из отчёта врача (нить task-dedup, 02.10).

    Почему снятые. Сверка в `save_task` смотрит только на открытые, и снятая сегодня
    задача рождалась заново на следующем обзоре: у партнёра 51 задача, 47 из них от
    еженедельного обзора, повторяли друг друга в 2–4 формулировках с июля. Снятие —
    решение человека, и писатель, который его не прочитал, это решение отменяет.

    Выполненные (`completed`) сюда не входят сознательно: сданный анализ и новая
    просьба врача о нём — новый цикл, а не повтор."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, type, status, content FROM tasks "
            "WHERE status IN ('open', 'snoozed') "
            "   OR (status='dismissed' AND julianday('now') - julianday(resolved_at) <= ?) "
            "ORDER BY created_at, id", (dismissed_window_days,)
        ).fetchall()
    return [dict(r) for r in rows]


def record_duplicate_skip(source: str, type_: str, content: str, source_date: str,
                          fingerprint: str | None, covered_ids: list[int],
                          judge_reason: str) -> int:
    """След того, что судья повторов НЕ создал задачу: строка со статусом 'duplicate'.

    Зачем в базе, а не в логе (нить task-dedup-tails, 04.10). Приёмка первого живого обзора
    не смогла проверить судью у владельца: отчёт пересобрали вне процесса бота, и единственный
    след — строка лога — не остался нигде. Пропуск, которого не видно, неотличим от «экстрактор
    не выделил задачу», а потеря просьбы врача — молчание, которое снаружи не видно.

    Статус 'duplicate' не видит человек: бот, дашборд и напоминания читают open/snoozed/answered
    (перепись 14 читателей tasks, 04.10). В множество «уже известно человеку» строка тоже не
    входит (get_tasks_known_to_person), так что след не порождает новых пропусков."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (source, source_date, type, content, status, resolved_at, "
            "resolved_text, fingerprint, judge_verdict, judge_reason) "
            "VALUES (?, ?, ?, ?, 'duplicate', datetime('now'), ?, ?, 'covered', ?)",
            (source, source_date, type_, content,
             "повтор: уже есть " + ", ".join(f"#{i}" for i in covered_ids),
             fingerprint, judge_reason))
        return cur.lastrowid


def get_task_by_tg_message(tg_message_id: int) -> dict | None:
    """Открытая задача, к сообщению которой прилетел реплай.

    Тот же образец, что pending_field_reviews.tg_message_id: привязка ответа к
    предмету — по квитанции доставки, а не по догадке о теме текста."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE tg_message_id=? AND status='open'",
            (tg_message_id,)
        ).fetchone()
    return dict(row) if row else None


def get_unsent_assessment_tasks() -> list:
    """Outbox опросников: открытые задачи type='assessment' с sent_at IS NULL.

    Планировщик (launchd 03:30, без бота) создаёт задачу; outbox должен
    доставить клавиатуру build_assessment_task_keyboard для заполнения.
    Закрытие задачи через дашборд само по себе не создаёт оценку в lab_results.
    Читатель — job бота (jobs.scheduled.deliver_unsent_assessment_tasks), тот же
    образец, что outbox заключений: метка по id, не по MAX.

    2026-09-12: тело переехало в get_unsent_tasks(type_) — имя осталось, потому что
    у него живые вызывающие; второго запроса под тем же смыслом заводить нельзя."""
    return get_unsent_tasks("assessment")


def wake_snoozed_assessment_tasks(today) -> int:
    """Отложенный опросник, чей срок настал, снова ложится в outbox: status='open', sent_at=NULL.

    До 28.09 «+3д / +7д» ставили status='snoozed' и новый deadline — и на этом всё:
    outbox берёт только open с sent_at IS NULL, планировщик считает snoozed живой задачей
    и нового не заводит. Отложенный опросник не возвращался никогда (холодное чтение
    сообщений 28.09). Дашборд уже считал «snoozed с прошедшим сроком» за открытую
    (dashboard_routers/views.py) — здесь то же правило для бота.
    today — дата в поясе тенанта (get_today), не date('now') SQLite в UTC.
    Возвращает число разбуженных задач."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='open', sent_at=NULL "
            "WHERE type='assessment' AND status='snoozed' AND deadline IS NOT NULL AND deadline <= ?",
            (str(today),)
        )
        return cur.rowcount


def mark_task_sent(task_id: int, tg_message_id: int | None = None):
    """Помечает что задача отправлена в Telegram.

    tg_message_id — квитанция доставки (telegram_bot::delivery_receipt_not_proxy):
    к этому сообщению прилетит реплай с ответом, по нему же ответ находит свою
    задачу. Без него у вопроса нет обратного адреса."""
    with _hdb.get_conn() as conn:
        if tg_message_id is None:
            conn.execute(
                "UPDATE tasks SET sent_at=datetime('now') WHERE id=?",
                (task_id,)
            )
        else:
            conn.execute(
                "UPDATE tasks SET sent_at=datetime('now'), tg_message_id=? WHERE id=?",
                (tg_message_id, task_id)
            )


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
