#!/usr/bin/env python3.11
"""
model_health_check.py — датчик жизненного цикла LLM-моделей.

Зачем: модели в hai_core.MODEL_DEFAULTS — внешний артефакт со своим EOL.
Когда Anthropic отзывает модель, вызовы падают, а фолбэк get_model спасает
только от падения БД, не от отзыва модели. Models API не отдаёт даты
deprecation (поля: id/capabilities/created_at/...), поэтому надёжный сигнал —
живой ping каждого ID. Алиас (claude-haiku-4-5) валиден для инференса, но
отсутствует в models.list(), поэтому presence-check дал бы ложную тревогу —
пингуем вызовом.

Принцип проекта: «fallback требует датчика», «тихий сбой обязан громко
сигналить». Один публичный entry point: run_check().
Запуск: ЕЖЕДНЕВНО из run_checks.sh (--daily --notify: снимок доступности для
get_model + самопереключение на следующую допущенную модель цепочки с уведомлением,
решение владельца 2026-10-01) и ежемесячно из monthly_api_report (только проверка).
До 2026-10-01 — только ежемесячно: отзыв модели мог молчать до месяца.
"""
import sys
import logging
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import anthropic
import hai_core

log = logging.getLogger(__name__)
_SECRETS = Path.home() / ".health_secrets"


def _alert(text: str) -> None:
    import notify
    import i18n
    notify.notify_operator(i18n.t("owner.card.model_retired"))


def _listed_ids(client) -> set[str]:
    """ID моделей из списка провайдера (бесплатный запрос). Нет метода/сбой — пусто:
    тогда доступность каждой модели решает пинг, как до 2026-10-01."""
    try:
        return {m.id for m in client.models.list(limit=100)}
    except Exception as e:  # silent-ok: падение списка покрывается пингом каждой модели ниже
        log.warning("model_health_check: список моделей недоступен (%s) — проверяю пингом", e)
        return set()


def _ping(client, mid: str) -> tuple[bool, str | None]:
    try:
        client.messages.create(task="model_health_check._ping", model=mid, max_tokens=4,
                               messages=[{"role": "user", "content": "ping"}])
        return True, None
    except Exception as e:  # silent-ok: surfaced via run_check
        import llm_client
        if isinstance(e, anthropic.NotFoundError) or llm_client.is_model_not_found(e):
            return False, "retired/not_found: " + str(e)[:140]
        if llm_client.is_account_problem(e):
            # Ключ или счёт у поставщика (карточка report:9198ad, v0.2.4) — о модели это не
            # говорит НИЧЕГО: ни «недоступна», ни «доступна». Пометка ACCOUNT — run_check не пишет
            # её в ремонт кода и не обновляет снимок доступности (иначе ложное переключение).
            log.warning("model_health_check: %s — поставщик отказал по ключу/счёту, о модели не судим", mid)
            return False, ACCOUNT + type(e).__name__
        return False, type(e).__name__ + ": " + str(e)[:140]


ACCOUNT = "account: "   # деталь пинга, отбитого по ключу/счёту: доступность модели НЕ проверена


def check_models(client=None) -> dict:
    """Для каждой роли: цепочка допущенных моделей, какие из них доступны, какая активна.

    Доступна = есть в списке провайдера ЛИБО отвечает на пинг (алиасы вроде
    claude-haiku-4-5 в список не входят). Активна = первая доступная в цепочке
    (hai_core.pick_from_chain — тот же выбор, что у get_model).
    Возврат: {role: {"model", "ok", "detail", "chain", "available"}}; доп. ключ "_available"."""
    client = client or hai_core.get_client()
    listed = _listed_ids(client)
    chains = {role: hai_core.model_chain(role) for role in hai_core.MODEL_DEFAULTS}
    status: dict[str, tuple[bool, str | None]] = {}
    for mid in dict.fromkeys(m for c in chains.values() for m in c):
        status[mid] = (True, None) if mid in listed else _ping(client, mid)
    available = {m for m, (ok, _) in status.items() if ok} | listed
    out = {}
    for role, chain in chains.items():
        active = hai_core.pick_from_chain(chain, available)
        ok, detail = status[active]
        out[role] = {"model": active, "ok": ok, "detail": detail, "chain": chain,
                     "available": [m for m in chain if m in available]}
    out["_available"] = sorted(available)
    out["_account"] = sorted(m for m, (_, d) in status.items() if str(d).startswith(ACCOUNT))
    return out


def _persist_and_announce(res: dict, notify: bool) -> list[tuple[str, str, str]]:
    """Снимок доступности для get_model + смена активной модели → уведомление владельцу.

    Решение владельца 2026-10-01: «сама с уведомлением» — система переключается на
    следующую ДОПУЩЕННУЮ модель цепочки без вопроса; человеку только сообщение."""
    from datetime import datetime, timezone
    import health_db as db
    db.upsert_config("llm.available", value_json={
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # time-inject: ok
        "ids": res["_available"]}, category="llm", source="model_health_check")
    switched = []
    for role, v in res.items():
        if role.startswith("_") or not v["ok"]:
            continue
        prev = db.get_config(f"llm.active.{role}")
        if prev and prev != v["model"]:
            switched.append((role, str(prev), v["model"]))
        if prev != v["model"]:
            db.upsert_config(f"llm.active.{role}", value_text=v["model"],
                             category="llm", source="model_health_check")
    if switched and notify:
        import i18n
        import notify as notifications
        for role, old, new in switched:
            chain = res[role]["chain"]
            back = old in chain and chain.index(new) < chain.index(old)   # вернулись на основную
            key = "owner.card.model_switched_back" if back else "owner.card.model_switched"
            notifications.notify_operator(i18n.t(key, old=old, new=new))
    return switched


def run_check(notify: bool = False, client=None, persist: bool = False) -> dict:
    """Публичная точка входа. notify=True → журнал отказов и карточки владельцу;
    persist=True (ежедневный запуск из run_checks.sh) → снимок доступности для
    get_model и уведомление о самопереключении. Возвращает полный отчёт."""
    import llm_client
    try:
        res = check_models(client=client)
    except llm_client.ProviderConfigurationError as exc:
        log.error("model_health_check: %s", exc)
        return {"configuration": {"ok": False, "detail": str(exc)}}
    roles = {r: v for r, v in res.items() if not r.startswith("_")}
    account = res.get("_account") or []
    if account:
        # Поставщик отказал по ключу/счёту: доступность этих моделей сегодня не проверена.
        # Снимок не трогаем (иначе переключение или «модель снова доступна» без основания),
        # в ремонт кода не пишем (чинить там нечего), владельцу установки — одна карточка.
        log.warning("model_health_check: проверка не состоялась по ключу/счёту: %s", account)
        if notify:
            try:
                import i18n
                import llm_client
                import notify as notifications
                notifications.notify_operator(i18n.t("owner.card.llm_account",
                                                     provider=llm_client.provider_title()))
            except Exception as e:
                log.error("model_health_check: карточка о ключе не отправлена: %s", e)
    if persist and not account:
        try:
            _persist_and_announce(res, notify)
        except Exception as e:
            log.error("model_health_check: снимок доступности не записан: %s", e)
            if notify:
                import notify as notifications
                notifications.fault("model_health_check: availability snapshot not written", person_key=None)
    failed = {r: v for r, v in roles.items()
              if not v["ok"] and not str(v["detail"]).startswith(ACCOUNT)}
    if failed:
        lines = [f"• {r}: {v['model']} — {v['detail']}" for r, v in failed.items()]
        msg = ("🔴 Health OS: в цепочке роли не осталось доступной модели:\n" + "\n".join(lines))
        log.error("model_health_check FAILED: %s", failed)
        if notify:
            try:
                import notify as notifications
                notifications.fault("model_health_check: model request failed", person_key=None)
                if any(str(v["detail"]).startswith("retired/not_found:") for v in failed.values()):
                    _alert(msg)
            except Exception as e:
                log.error("model_health_check: alert send failed: %s", e)
    else:
        log.info("model_health_check OK: %s", {r: v["model"] for r, v in roles.items()})
    return roles


if __name__ == "__main__":
    import json
    report = run_check(notify="--notify" in sys.argv, persist="--daily" in sys.argv)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if all(v["ok"] for v in report.values()) else 1)
