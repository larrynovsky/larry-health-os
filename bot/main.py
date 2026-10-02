"""
bot/main.py — entry-point Telegram-бота: build Application, register handlers/jobs, run_polling.

Sprint 6 C12 (2026-05-23): финальная декомпозиция telegram_bot.py.
Все домены — в handlers/, jobs/, services/, bot/.

Порядок регистрации handler-ов важен:
  1. meta, reports — простые command handlers
  2. handlers.consult (ConvHandler /consult) — ДО handlers.messages
  3. tasks, problems, genome, hypotheses — простые command handlers
  4. handlers.messages — ПОСЛЕ ConvHandler чтобы текст в state CONSULTING
     шёл в cmd_consult_continue, а не в handle_text
  5. error_handler
  6. jobs.scheduled — все JobQueue tasks
  7. handlers.callbacks — CallbackQueryHandler (inline-кнопки)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Гарантия что родительская директория в path для абсолютных импортов
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram.ext import Application  # noqa: E402

import health_ai as ai  # noqa: E402
import health_db as db  # noqa: E402

# Резолв owner_chat_id — ленивый (не при импорте). Fail-closed на старте —
# assert_owner_configured() в main(), не на импорте (иначе сбор тестов хрупок).
from bot.errors import _error_handler  # noqa: E402
from bot.filters import (  # noqa: E402
    _owner_filter, assert_owner_configured, get_token, owner_chat_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / "health_bot.log"),
        logging.StreamHandler()
    ]
)
# httpx/httpcore логируют полный URL вида /bot<TOKEN>/getUpdates — убираем
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def main():
    # Fail-closed на старте: без owner_chat_id бот не поднимается
    # («первый встречный не станет владельцем»). Замена module-level raise.
    assert_owner_configured()
    # Явная квитанция резолва в лог — будущий рестарт оставляет доказательство,
    # что старт-гейт прошёл и на КАКОМ owner (не значение секрета, а chat_id — он
    # и так в каждом send_message; PHI это не является).
    log.info(f"owner resolved: {owner_chat_id()}")

    db.init_db()
    ai._ensure_history_table()

    app = build_app(get_token())
    log.info("Бот запущен (Claude API backend)")
    app.run_polling(drop_pending_updates=True)


def build_app(token: str, request=None) -> Application:
    """Приложение бота со всеми обработчиками — ровно то, что слушает Telegram.

    Отдельно от main(), чтобы проверка первого контакта (scripts/first_contact_smoke.py)
    гоняла ТЕ ЖЕ обработчики, фильтры и обработчик ошибок, а не свою копию регистрации:
    копия разъехалась бы с ботом молча. `request` — подмена сетевого слоя Telegram
    (в проверке — поддельный, в работе — None, то есть настоящий).
    """
    # Outbox заключений консилиума: на старте — и далее каждые 5 мин
    # (jobs.scheduled.deliver_unsent_outcomes_job). Переехало из этого
    # файла 2026-08-29: одна функция, два вызова.
    from jobs.scheduled import deliver_unsent_outcomes
    builder = Application.builder().token(token).post_init(deliver_unsent_outcomes)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()

    owner = _owner_filter()

    # ── Handlers — порядок важен ──────────────────────────────────────────
    # meta: start, help, app, memory, checkin, sleep, experiment
    import handlers.meta as _meta_h
    _meta_h.register(app, owner)

    # reports: report, weekly, monthly, labs
    import handlers.reports as _reports_h
    _reports_h.register(app, owner)

    # /consult ConversationHandler — ДО handlers.messages
    # чтобы текст в state CONSULTING шёл в cmd_consult_continue, а не в handle_text
    import handlers.consult as _consult_h
    _consult_h.register(app, owner)

    # tasks: tasks, done, dismiss, visit
    import handlers.tasks as _tasks_h
    _tasks_h.register(app, owner)

    # problems: problems, approve, reject
    import handlers.problems as _problems_h
    _problems_h.register(app, owner)

    # genome: genome, genome_update
    import handlers.genome as _genome_h
    _genome_h.register(app, owner)

    # hypotheses: hypotheses, confirm, hreject, protocols, retire, hyp
    import handlers.hypotheses as _hyp_h
    _hyp_h.register(app, owner)

    # symptom-intake (visual) — ДО messages (перехватывает фото с подписью-жалобой).
    # Гейт флага: регистрируется ТОЛЬКО при symptom_intake_enabled (WP4, dark by default).
    # fail-safe: ошибка чтения флага/импорта не должна ронять старт бота.
    try:
        import config_db as _cfg
        if _cfg.get_config("symptom_intake_enabled", False):
            import handlers.symptom as _sym_h
            _sym_h.register(app, owner)
            log.info("symptom-intake handler registered (flag ON)")
    except Exception as _sym_e:
        log.warning(f"symptom-intake registration skipped: {_sym_e}")

    # messages: text, photo, location — ПОСЛЕ ConvHandler /consult
    import handlers.messages as _msgs_h
    _msgs_h.register(app, owner)

    # Глобальный error handler
    app.add_error_handler(_error_handler)

    # ── Scheduled jobs ────────────────────────────────────────────────────
    import jobs.scheduled as _jobs_h
    _jobs_h.register(app)

    # ── Inline-button callbacks ───────────────────────────────────────────
    import handlers.callbacks as _cb_h
    _cb_h.register(app)
    import bot.actions as _actions
    _actions.register(app)
    return app


def run(entry=None) -> None:
    """main() с трассировкой падения без значения токена.

    PTB кладёт токен в текст InvalidToken/ссылки запроса, а трассировка падения идёт в stderr —
    то есть в bot_err.log человека (прогон урока 30.09; журнал прикладывают к вопросам на GitHub).
    Слово InvalidToken остаётся: по нему урок и install.sh узнают неверный токен.
    """
    import sys
    import traceback
    try:
        (entry or main)()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — любой крах печатается, но очищенным
        text = traceback.format_exc()
        try:
            tok = get_token()
        except Exception:  # silent-ok: токена нет — вычищать нечего
            tok = ""
        if tok:
            text = text.replace(tok, "<telegram_token>")
        sys.stderr.write(text)
        sys.exit(1)


if __name__ == "__main__":
    run()
