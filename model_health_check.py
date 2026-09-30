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
Запуск: ежемесячно (рядом с отчётом по тратам) + вручную с --notify.
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


def check_models(client=None) -> dict:
    """Пингует каждый ID из MODEL_DEFAULTS живым вызовом.
    Возврат: {role: {"model": id, "ok": bool, "detail": str|None}}."""
    client = client or hai_core.get_client()
    out = {}
    for role, mid in hai_core.MODEL_DEFAULTS.items():
        try:
            client.messages.create(
                model=mid, max_tokens=4,
                messages=[{"role": "user", "content": "ping"}],
            )
            out[role] = {"model": mid, "ok": True, "detail": None}
        except anthropic.NotFoundError as e:  # silent-ok: surfaced via run_check
            out[role] = {"model": mid, "ok": False,
                         "detail": "retired/not_found: " + str(e)[:140]}
        except Exception as e:  # silent-ok: surfaced via run_check
            out[role] = {"model": mid, "ok": False,
                         "detail": type(e).__name__ + ": " + str(e)[:140]}
    return out


def run_check(notify: bool = False, client=None) -> dict:
    """Публичная точка входа. notify=True → журнал отказов; недоступная модель требует выбора замены.
    Возвращает полный отчёт (для тестов/логов)."""
    res = check_models(client=client)
    failed = {r: v for r, v in res.items() if not v["ok"]}
    if failed:
        lines = [f"• {r}: {v['model']} — {v['detail']}" for r, v in failed.items()]
        msg = ("🔴 Health OS: LLM-модель недоступна (отозвана?). "
               "Нужна правка MODEL_DEFAULTS + деплой:\n" + "\n".join(lines))
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
        log.info("model_health_check OK: %s",
                 {r: v["model"] for r, v in res.items()})
    return res


if __name__ == "__main__":
    import json
    report = run_check(notify="--notify" in sys.argv)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if all(v["ok"] for v in report.values()) else 1)
