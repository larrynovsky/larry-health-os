"""Живость подъёмника вопросов: первый сигнал о смерти механизма, а не последний.

Зачем датчик существует при уже построенном датчике потерь. «Кандидат умер, не
побывав у судьи» увидит смерть подъёмника — но только когда первый неувиденный
кандидат пересечёт окно свежести, то есть через 45 дней. Разница между «узнать через
двое суток» и «узнать через полтора месяца» и есть смысл этой работы.

Три случая, как у двух соседних liveness-датчиков: свежая квитанция → тихо, нет
квитанции → warn, протухшая → warn. Четвёртый тест стережёт замысел, который легко
потерять при правке: датчик судит ФАКТ ЗАПУСКА, а не число поднятого. Разговоры с
ботом редкие, и «сегодня поднято ноль» — норма; датчик, краснеющий на норме,
перестаёт читаться.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

KEY = "_promote_questions_last_run"


def _hb(hours_ago: float, every_s: int = 86400, raw=None) -> None:
    """Поставить квитанцию: запуск hours_ago часов назад, объявленный ритм every_s (23.09:
    ритм объявляет сам джоб — jobs/scheduled._rhythm). raw — квитанция как есть (битая)."""
    import json
    from datetime import timedelta
    import health_db
    import integrity_tests as it
    started = (it.get_utcnow() - timedelta(hours=hours_ago)).isoformat() + "+00:00"
    vj = raw if raw is not None else json.dumps({"every_s": every_s, "started_at": started})
    with health_db.get_conn() as c:
        c.execute(
            "INSERT INTO system_config (key, value_text, value_json, updated_at) "
            f"VALUES ('{KEY}', 'ok', ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (vj,))


def _run():
    """ВСЕ предупреждения датчика, без фильтра по тексту.

    Фильтр по подстроке был бы удобнее, но он же и ослепил бы тесты: датчик, который
    вдобавок к своему делу начал бы кричать о чём-то ещё, прошёл бы мимо. Поймано
    мутацией «судим урожай»: под фильтром она роняла не тот тест."""
    import integrity_tests as it
    it._warnings.clear()
    it.check_promote_questions_liveness()
    return list(it._warnings)


def test_silent_when_fresh(db):
    """Свежая квитанция — молчим. Позитивный контроль: без него тест на красноту
    ничего не значит, потому что датчик, который всегда красный, тоже «ловит».

    Канал здесь НЕ пустой (есть заданный вопрос) — нарочно: так этот случай
    отличается от «тихого дня» ниже ровно одним признаком, и мутация «судить урожай»
    роняет тот тест, а не этот."""
    _hb(0)
    db.add_task("Какую дозу вы принимаете?", type="question")
    assert not _run(), "свежая квитанция не должна варнить"


def test_warns_when_never_ran(db):
    """Квитанции нет вовсе. Это либо джоб не запускался, либо падает ДО записи —
    оба случая означают, что кандидаты копятся, и человека перестали спрашивать."""
    fired = _run()
    assert any("ни разу не отметился" in w[0] for w in fired), fired


def test_warns_after_the_first_missed_run(db):
    """Решение владельца 23.09 «общее правило»: тревога после ПЕРВОГО пропуска. Суточный
    джоб, последний старт 25 ч назад — один запуск пропущен. До 23.09 порог был 48 ч, и
    этот случай молчал."""
    _hb(25)
    fired = _run()
    assert any("пропустил запуск" in w[0] for w in fired), fired


def test_silent_just_before_the_next_run(db):
    """Негативный контроль к тесту выше: 23 ч после старта суточного джоба — следующий
    запуск ещё не был должен. Порог из объявленного ритма, а не из литерала: тот же
    возраст при ритме в 12 ч — уже пропуск."""
    _hb(23)
    assert not _run()
    _hb(23, every_s=12 * 3600)
    assert any("пропустил запуск" in w[0] for w in _run())


def test_receipt_without_declared_rhythm_is_loud(db):
    """Квитанция без объявленного ритма (или битая) — «не судимо» вслух, не тишина."""
    _hb(0, raw="не-json")
    assert any("ритм не объявлен" in w[0] for w in _run())


def test_silent_on_a_quiet_day_with_zero_lifted(db):
    """ЗАМЫСЕЛ, а не механика: свежая квитанция при полностью пустом канале —
    ни одного кандидата, ни одной задачи-вопроса — обязана читаться как норма.

    Это негативный контроль к соблазну судить урожай: если однажды кто-то заменит
    предикат на «сколько поднято за сутки», этот тест покраснеет. Замер, из-за
    которого он нужен: 13.09 разговоры дали 14 кандидатов в один день и ноль в
    последующие двенадцать — тишина здесь штатна.
    """
    _hb(1)
    import health_db
    with health_db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert not _run(), "тихий день объявлен поломкой — датчик судит урожай, а не запуск"


def test_receipt_is_written_after_the_work_not_before():
    """Упавший подъём не оставляет квитанции.

    Судится ИСХОДНИК джоба, а не поведение: поднять его в тесте значило бы поднять
    telegram-приложение и очередь задач, то есть проверять оснастку. Проверка узкая и
    её граница названа: она ловит перестановку строк (квитанция раньше вызова) и
    вынос записи из try, но не докажет, что джоб вообще работает.
    """
    import inspect

    from jobs import scheduled

    src = inspect.getsource(scheduled.promote_memory_questions_job)
    body = src.split('"""')[-1]
    call = body.index("promote_memory_questions)")
    receipt = body.index(KEY)
    assert call < receipt, (
        "квитанция ставится РАНЬШЕ работы — датчик позеленеет на факте входа в "
        "функцию, а не на факте подъёма")
    assert body.index("except Exception") > receipt, (
        "запись квитанции вынесена из try — упавший подъём оставит отметку «жив»")
