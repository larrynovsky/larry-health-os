"""owner_gate.py — структурный классификатор: обязано ли предложенное действие
уйти НА РЕШЕНИЕ ВЛАДЕЛЬЦУ (fail-closed), или его можно авто-починить/подготовить.

LIVE-механизм §13-клаузы (сверка §11 2026-08-02): автономный актор может ТОЛЬКО
понизить действие до парковки владельцу, никогда повысить до авто. Разграничение
СТРУКТУРНОЕ, не по уверенности агента.

Классифицирует РЕШЕНИЕ, не ДОСТУП (иначе паркует диагностические чтения канона и
ломает §13 «не эскалируй автоматизируемое»). Поэтому смотрит на ЗАПИСЬ (writes) и
необратимость, а не на факт чтения.

Безопасность держит НЕ полнота списка доменных таблиц, а ЗАКРЫТЫЙ allowlist
авто-категорий: чтобы действие пошло в авто, оно обязано (а) быть в AUTO_CATEGORIES
И (б) не писать в домен владельца И (в) быть обратимым. Не выполнено хоть одно —
парк. Неизвестная категория — парк. Так неполный список доменов не открывает дыру.

Доменные решения он НЕ принимает и НЕ хранит — только МАРКИРУЕТ; сами дома
(lab_domain_verdicts §16, пороги §9, field_reviews_db) остаются оракулами.
"""
from __future__ import annotations

from typing import Optional

# Закрытый allowlist: ЕДИНСТВЕННЫЕ категории, которым авто/подготовка разрешены.
# Всё вне списка — парк владельцу (fail-closed). Расширять — решением владельца.
AUTO_CATEGORIES = ("dev_fix", "test_fix", "ops_config")

# Запись в эти таблицы канона/порогов = решение владельца (§16, §9). Список —
# ВТОРИЧНЫй guard (ловит авто-категорию, тайком пишущую канон); первичная защита —
# закрытый AUTO_CATEGORIES выше. Делегирует домам, не заменяет их.
OWNER_DOMAIN_WRITES = ("lab_results", "lab_domain_verdicts", "lab_results_staging")


def _writes_owner_domain(writes) -> list:
    """Какие из целей записи попадают в домен владельца. Подстрочное совпадение:
    'lab_results' ловит и 'health.db:lab_results', и 'lab_results_staging' явно."""
    hits = []
    for w in (writes or []):
        wl = str(w).lower()
        if any(dom in wl for dom in OWNER_DOMAIN_WRITES):
            hits.append(w)
    return hits


def owner_is_the_oracle(action: dict) -> tuple[bool, Optional[str]]:
    """Есть ли у действия СТРУКТУРНАЯ причина быть решением владельца: оно необратимо
    или пишет в его домен. Уже, чем requires_owner: «категория вне allowlist» здесь
    причиной не считается, и неизвестная категория — тоже.

    Зачем отдельно (23.09). Ночной цикл спрашивал requires_owner и о находках, которые
    реестр классов УЖЕ назвал инженерными (класс fix). Судья-LLM пишет категорию
    свободным словом, всё вне трёх разрешённых уходило владельцу — и на столе лежали
    «сохранить файл в общую систему?» и «пересчитать показатели сейчас?». Для таких
    находок владельцу уходит только то, что названо здесь; остальное — в инженерную
    очередь. Это не повышение прав: инженерная очередь ничего не исполняет сама."""
    if action.get("irreversible"):
        return True, "необратимое действие (§13): оракул — только владелец"
    hits = _writes_owner_domain(action.get("writes"))
    if hits:
        return True, f"пишет в домен владельца {hits} (канон §16 / порог §9) — делегируй в его дом"
    return False, None


def requires_owner(action: dict) -> tuple[bool, Optional[str]]:
    """Обязано ли действие уйти владельцу. Возврат (True, причина) → парк; (False, None) → авто.

    action:
      category   — что за действие (dev_fix/test_fix/ops_config → кандидат в авто; иначе парк)
      writes     — список целей записи (таблицы/пути); запись в домен владельца → парк
      irreversible — bool; необратимое (delete/promote) → парк всегда

    Fail-closed: отсутствующая/неизвестная категория → парк. Агент этим может лишь
    ПОНИЗИТЬ до владельца, не повысить: 'auto' выдаётся только при всех трёх условиях.
    """
    cat = action.get("category")
    if cat is None:
        return True, "категория не указана — fail-closed к владельцу"
    owner, why = owner_is_the_oracle(action)
    if owner:
        return True, why
    if cat in AUTO_CATEGORIES:
        return False, None
    return True, f"категория {cat!r} вне закрытого AUTO_CATEGORIES — fail-closed к владельцу"


def silence_default_allowed(action: dict) -> tuple[bool, Optional[str]]:
    """Можно ли ЭТУ карточку закрыть умолчанием, если владелец промолчит 2 недели.

    Решение владельца 2026-09-13 (вариант В): «молчание = делегирование, НО не
    применяется к медицинскому и необратимому». Здесь ровно эта граница, и она
    УЖЕ, чем requires_owner: карточка лежит на столе владельца по одной из трёх
    причин, и делегируемы не все три.

      • необратимое              → НИКОГДА (откатить нечем, цена молчания вечна);
      • пишет в домен владельца  → НИКОГДА (канон §16 / порог §9 — его оракул);
      • категория вне allowlist  → делегируемо: сюда попадает всё, что просто НЕ
        входит в закрытый список авто-категорий. Fail-closed до его слова —
        законно; вечное ожидание его слова — нет, это и есть ящик в стол.

    Отсутствующая категория → НЕТ: «мы не знаем, что это» и «мы знаем, что это
    безобидно» — разные утверждения, и умолчание имеет право только на второе.

    Возврат (True, None) → карточке можно выдать тройку умолчания;
            (False, причина) → нельзя, и причина едет в лог, а не в тишину."""
    if action.get("category") is None:
        return False, "категория не указана: неизвестное не делегируется молчанием"
    if action.get("irreversible"):
        return False, "необратимое (§13): молчание не заменяет решения владельца"
    hits = _writes_owner_domain(action.get("writes"))
    if hits:
        return False, f"пишет в домен владельца {hits} — оракул только он (§16/§9)"
    return True, None


if __name__ == "__main__":
    # Самопроверка: неизвестная категория и запись в канон паркуются; чистый dev_fix — нет.
    assert requires_owner({"category": "promote_canon"})[0] is True
    assert requires_owner({"category": "dev_fix", "writes": ["lab_results"]})[0] is True
    assert requires_owner({"category": "dev_fix", "writes": ["tests/x.py"]})[0] is False
    assert requires_owner({"category": "dev_fix", "irreversible": True})[0] is True
    # Граница умолчания УЖЕ, чем граница парковки: делегируемо только «просто вне
    # allowlist», но не необратимое, не канон и не «категория неизвестна».
    assert silence_default_allowed({"category": "promote_canon"})[0] is True
    assert silence_default_allowed({"category": "promote_canon", "irreversible": True})[0] is False
    assert silence_default_allowed({"category": "promote_canon", "writes": ["lab_results"]})[0] is False
    assert silence_default_allowed({})[0] is False
    # Структурная причина: необратимое и канон — да; «категория вне списка» — нет.
    assert owner_is_the_oracle({"category": "promote_canon"})[0] is False
    assert owner_is_the_oracle({"irreversible": True})[0] is True
    assert owner_is_the_oracle({"writes": ["lab_domain_verdicts"]})[0] is True
    print("owner_gate selftest ok")
