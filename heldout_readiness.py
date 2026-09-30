"""Гаситель нити validation-gate-repair: что поднять владельцу, когда ДАННЫЕ оживляют held-out/лаги —
а не когда человек вспомнит (§18: отложенное «решим в сентябре» без гасителя схлопывается в тишину).

Зачем отдельный модуль, а не функция в integrity_tests: там `check()` исполняет функцию СРАЗУ при
импорте, поэтому чистое ядро тестируется только в своём доме. Здесь — ТОЛЬКО решение (детерминированное,
без БД/времени/веры); сбор входов и парковку владельцу делает тонкий `check_heldout_ready` в
integrity_tests (он читает frozen_at, дату, q_lag из веры и зовёт parks_due + parked_decisions.park).
"""
from __future__ import annotations
import i18n


def parks_due(weeks_since_freeze, q_lag_passed, floor_weeks, frozen_at=None, *, lang="ru"):
    """Какие решения поднять владельцу СЕЙЧАС. Возврат [(gate_id, summary)], пустой — нечего.

    Два независимых спусковых оживления нити:
      (a) held-out дозрел: пост-freeze окно >= пола → «читай confirm-вердикт в вере».
          С 2026-08-19 confirm АВТОМАТИЧЕН в каждом прогоне гейта (нить heldout, семантика A) —
          парк зовёт не запускать, а ПРОЧЕСТЬ gate_meta.heldout и решить судьбу переживших.
          Пол 42д — замеренная граница валидности перестановочного нуля (Э1a), не назначение.
      (b) q_lag-выживший в вере (passed>0) → §6.1: допускать ли лаг-сигнал в генеративный слой.

    frozen_at (ISO-строка) едет В КЛЮЧ гейта (a): вопрос «дозрело ли окно» задаётся про
    конкретный freeze, значит и ключ обязан нести freeze (один вопрос — один ключ, решение
    владельца 2026-08-23). Без этого resolved по старому freeze глушил бы звонок нового
    НАВСЕГДА — check_heldout_ready resolved не переоткрывает намеренно. Ключ (b) freeze
    НЕ несёт: промоут в генеративный слой — standing-политика владельца, не свойство окна;
    перезвонить про решённую политику после пере-freeze = нытьё, а не гаситель.

    Чистая функция: тот же вход — тот же выход, без побочных эффектов. Оракул — __main__ ниже."""
    gates = []
    if (weeks_since_freeze is not None and floor_weeks is not None
            and weeks_since_freeze >= floor_weeks):
        gates.append((
            "vg:heldout_ready" + (f":{frozen_at}" if frozen_at else ""),
            i18n.t("owner.card.heldout", lang=lang)))
    if q_lag_passed and q_lag_passed > 0:
        gates.append((
            "vg:signal_generative_promotion",
            i18n.t("owner.card.lag", lang=lang)))
    return gates


if __name__ == "__main__":
    # Оракул: каждая ветка гасителя краснеет при поломке порога/условия.
    assert parks_due(5, 0, 6) == [], "рано и лагов нет — ничего не поднимаем"
    assert [g for g, _ in parks_due(6, 0, 6)] == ["vg:heldout_ready"], "дозрело ровно на пол"
    assert [g for g, _ in parks_due(8, 0, 6)] == ["vg:heldout_ready"], "с запасом"
    assert [g for g, _ in parks_due(3, 2, 6)] == ["vg:signal_generative_promotion"], "лаг-выживший рано"
    assert [g for g, _ in parks_due(6, 1, 6)] == [
        "vg:heldout_ready", "vg:signal_generative_promotion"], "оба спусковых разом"
    assert parks_due(None, None, 6) == [], "нет данных → нечего судить (fail-open)"
    assert [g for g, _ in parks_due(6, 0, 6, frozen_at="2026-07-12")] == [
        "vg:heldout_ready:2026-07-12"], "ключ heldout обязан нести freeze"
    assert (parks_due(6, 1, 6, frozen_at="2026-07-12")[1][0]
            == "vg:signal_generative_promotion"), "ключ §6.1 freeze НЕ несёт (standing-политика)"
    print("heldout_readiness selftest ok")
