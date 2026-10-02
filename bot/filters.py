"""
bot/filters.py — owner_chat_id(), _owner_filter, secrets paths.

Резолв owner chat_id — ЛЕНИВЫЙ и МЕМОИЗИРОВАННЫЙ (owner_chat_id()), НЕ при импорте.
Причина (2026-07-17): module-level резолв с fail-closed raise делал ВЕСЬ сбор
`pytest tests/` хрупким к утечке env — любой протекающий тест, перекрывший
HEALTH_SECRETS_DIR, ронял импорт бот-цепочки на этапе collection (инцидент
2026-07-15: ложно-красный утренний гейт). Ленивый резолв закрывает этот КЛАСС.

Инварианты (fail-closed на ОБОИХ концах):
  - Старт: assert_owner_configured() — бот не поднимается без владельца
    («первый встречный не станет владельцем»). Зовётся в bot/main.main().
  - Чтение: owner_chat_id() при отсутствии секрета RAISE, НИКОГДА не None —
    иначе auth-чек `id != None` короткозамкнётся в fail-OPEN (пускает чужого).
Единственный резолвер каталога секретов — secrets_paths.secrets_dir()
(дубль значения = split-brain маршрутизации; датчик test_secrets_single_resolver).

Восстановление: положи chat_id в <secrets_dir>/telegram_chat_id вручную.
Вынесено из telegram_bot.py в Sprint 6 (C1, 2026-05-23).
"""
# INTENT: telegram_bot — бот: fail-closed доставка владельцу (доступ/тенант/квитанция).
#          Замысел и инварианты — subsystem_intent.yaml, раздел telegram_bot.
from __future__ import annotations

from telegram.ext import filters

# Multitenancy (2026-06-30): секреты тенанта — из HEALTH_SECRETS_DIR
# (default ~/.health_secrets = канон владельца). Процесс партнёра задаёт
# HEALTH_SECRETS_DIR=~/.health_secrets_partner → его токен/чат. Без fallback
# на чужой каталог: отсутствие файла = fail-closed ниже, не подмена.
# Резолвер ЕДИНЫЙ — secrets_paths.secrets_dir(); локальная копия логики
# запрещена (дубль значения = split-brain; датчик test_secrets_single_resolver).
from secrets_paths import secrets_dir

_SECRETS_DIR = secrets_dir()
TOKEN_FILE   = _SECRETS_DIR / "telegram_token"
CHAT_ID_FILE = _SECRETS_DIR / "telegram_chat_id"

# Мемо-кэш owner chat_id. Снимок при первом резолве; смена chat_id (через
# --setup) требует рестарта бота (staleness до рестарта — задокументировано,
# не регресс: chat_id владельца — не меняющаяся в рантайме величина).
_OWNER_CHAT_ID: int | None = None


def get_token() -> str:
    return TOKEN_FILE.read_text().strip()


def get_chat_id() -> int | None:
    return int(CHAT_ID_FILE.read_text().strip()) if CHAT_ID_FILE.exists() else None


def owner_chat_id() -> int:
    """Owner chat_id — ленивый + мемоизированный резолв.

    RAISE при отсутствии секрета — НИКОГДА не возвращает None. None в auth-чеке
    `id != owner` короткозамкнулся бы в fail-OPEN (пускает не-владельца).

    API и режимы отказа: docs/reference/owner_chat_id_resolver.md — оттуда же
    почему резолв ленивый и как безопасно добавить консьюмера.
    """
    global _OWNER_CHAT_ID
    if _OWNER_CHAT_ID is None:
        if not CHAT_ID_FILE.exists():
            raise RuntimeError(
                f"OWNER_CHAT_ID не найден: {CHAT_ID_FILE}\n"
                "Положи свой Telegram chat_id в этот файл вручную."
            )
        _OWNER_CHAT_ID = int(CHAT_ID_FILE.read_text().strip())
    return _OWNER_CHAT_ID


def assert_owner_configured() -> None:
    """Старт-гейт (fail-closed): поднять RuntimeError, если владелец не сконфигурирован.

    Зовётся в bot/main.main() до run_polling — бот не поднимается без владельца.
    Замена module-level fail-closed raise, который делал импорт хрупким."""
    owner_chat_id()


def _owner_filter() -> filters.BaseFilter:
    """Фильтр: разрешаем только владельца. Fail-closed — filters.ALL никогда не возвращается."""
    return filters.Chat(chat_id=[owner_chat_id()])
