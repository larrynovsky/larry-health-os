"""L0a: обогащение машинного алерта указателем на карту задетой подсистемы.

Идея (SН-план, слой L0a): когда система сама заметила поломку (упавший тест,
integrity-алерт), в ТЕКСТ артефакта, который потом читает агент, вклеивается
компактный preflight задетой подсистемы. «genome_update лог пуст» приходит уже
с картой — читать её не выбор, а часть входных данных. Данные, не хук → работает
в Cowork (класс C-05: система заметила → артефакт → расследую).

Сигнал берём из ТРЕЙСБЕКА/ошибки, а не из имени теста: файл теста зовётся
`test_X.py` и не содержит стем модуля-под-тестом, а трейсбек несёт реальные
`File "gp_agent.py", line ..., in morning_brief` — оттуда стемы матчатся честно.

Контракт: НИКОГДА не бросает и не пуст-ломает вызывающего. Доставка/запись
артефакта важнее обогащения — любой сбой индекса → вернуть '' (алерт уходит как есть).
"""
from __future__ import annotations
import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def pointer_for(text: str, root: str = ".", max_subs: int = 2) -> str:
    """text (сообщение+трейсбек алерта) → компактный указатель на preflight
    задетых подсистем, или '' если ничего не сматчилось / при любом сбое."""
    try:
        from project_context import indexer, receipt
        ix = indexer.build(root)
        rmap = receipt._reverse_map(ix)          # стем модуля → {intent-id,...}
        toks = set(_WORD.findall(text or ""))
        subs: list[str] = []
        for stem in sorted(rmap):                # детерминированный порядок
            if stem in toks:
                for sid in sorted(rmap[stem]):
                    if sid not in subs:
                        subs.append(sid)
        subs = subs[:max_subs]
        if not subs:
            return ""
        blocks = [indexer.preflight(ix, s, budget=True) for s in subs]
        return ("\n\n📍 Карта задетых подсистем (маршрут, не замена чтению — "
                "открой файлы, сверь у источника):\n" + "\n".join(blocks))
    except Exception:  # silent-ok: обогащение best-effort, не смеет ронять алерт
        return ""
