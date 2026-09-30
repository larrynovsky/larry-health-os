"""Ось «выброшенного кандидата»: потеря — это НЕУВИДЕННОЕ, а не отфильтрованное.

Датчик должен различать кандидатов, не дошедших до судьи, и осознанно
отклонённые вопросы. Сравнение чисел без общей когорты смешивает
разные события и принимает исправную фильтрацию за потерю.
Датчик утечки, чей числитель растёт от исправной фильтрации, звонит тем громче, чем
лучше механизм работает.

Ось сменена решением владельца 14.09: судится ФАКТ «кандидат выпал за окно, ни разу
не побывав у судьи», а не отношение двух маленьких чисел. Порога у факта нет — это
важно при редких всплесках разговоров, где отношение считать не на чем.

Разделение труда между двумя половинами файла:
  • половина «след» проверяет ПРИЧИНУ — судья обязан оставлять отметку, иначе
    отклонённый неотличим от неувиденного;
  • половина «датчик» проверяет СЛЕДСТВИЕ на живой схеме БД — кто попадает в
    числитель, а кто нет.
Негативный контроль назван явно: без ретайра в первой половине вторая зеленеет
ложно, потому что отклонённый остаётся активным.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

WINDOW = 45


# ── Половина 1: судья обязан оставить след ──────────────────────────────────

def _promote_env(monkeypatch, verdicts: dict, retired: list):
    """Минимальная обвязка подъёмника: чисел из БД не читаем, судью подменяем."""
    import task_agent as ta
    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": WINDOW,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 5}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr("health_db.get_open_questions", lambda: [])
    monkeypatch.setattr("memory_facts_db.get_facts", lambda cls, since_days=None: [
        {"id": 4107, "value": "How does a queue select the next pending item?",
         "source": "conversation"},
    ])
    monkeypatch.setattr("memory_facts_db.retire_fact",
                        lambda fid, reason: retired.append((fid, reason)) or True)
    monkeypatch.setattr("health_db.save_task", lambda **kw: 999)
    monkeypatch.setattr("health_db.get_conn", _EmptyTakenConn)
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: verdicts)
    return ta


class _EmptyTakenConn:
    """Соединение-заглушка: у подъёмника один SELECT — уже занятые отпечатки."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **kw):
        return []


def test_rejected_candidate_is_retired(monkeypatch):
    """Явный вердикт «не пациенту» снимает кандидата с активных.

    До 14.09 эта ветка только писала в лог. Кандидат оставался активным и
    неотличимым от того, кого судья не видел ни разу, — и через окно попадал
    в числитель датчика утечки наравне с настоящей потерей.
    """
    retired: list = []
    ta = _promote_env(monkeypatch, {
        "4107": {"verdict": "not_patient", "reason": "вопрос к науке, не к человеку",
                "ru": ""},
    }, retired)

    assert ta.promote_memory_questions() == []
    assert [f for f, _ in retired] == [4107], (
        f"отклонённый судьёй не снят с активных: {retired}")
    assert "не пациенту" in retired[0][1], (
        f"причина ретайра не называет судью — разобрать потом будет нечем: {retired}")


def test_unparsed_verdict_does_not_bury_the_candidate(monkeypatch):
    """Объект разобрался, а поля вердикта в нём не было — это НЕ суждение.

    Граница дорогая и несимметричная: лишний вопрос стоит одного сообщения,
    потерянный — молчания, которого снаружи не видно. Поэтому артефакт разбора
    оставляет кандидата в живых, хотя вопрос по нему и не задаётся.
    """
    retired: list = []
    ta = _promote_env(monkeypatch, {
        "4107": {"verdict": "unparsed", "reason": "", "ru": ""},
    }, retired)

    assert ta.promote_memory_questions() == []
    assert retired == [], (
        f"кандидат похоронен по артефакту разбора, а не по суждению: {retired}")


def test_unsure_never_reaches_the_retire_branch(monkeypatch):
    """Сомнение судьи разрешается ВОПРОСОМ (решение владельца 12.09), значит до
    ветки ретайра оно не доходит вовсе. Тест стережёт связку двух решений: если
    `unsure` когда-нибудь выпадет из ASKING_VERDICTS, он молча начнёт хоронить."""
    retired: list = []
    saved: list = []
    ta = _promote_env(monkeypatch, {
        "4107": {"verdict": "unsure", "reason": "непонятно, к кому",
                "ru": "Актуален ли ещё этот вопрос?"},
    }, retired)
    monkeypatch.setattr("health_db.save_task",
                        lambda **kw: saved.append(kw["content"]) or 701)

    ta.promote_memory_questions()
    assert retired == [], f"сомнение похоронено вместо того, чтобы стать вопросом: {retired}"
    assert saved, "сомнение не превратилось в вопрос"


def test_missing_verdict_field_parses_as_unparsed_not_rejection():
    """Дефолт разбора — «unparsed», а не «not_patient».

    Оба одинаково запрещают спрашивать, но только второй даёт право хоронить.
    Сделать дефолтом отказ значило бы, что кривой ответ модели молча выносит
    приговор кандидату.
    """
    import task_agent as ta

    out = ta._verdicts_from_rows([{"id": "a", "reason": "", "ru": ""}])
    assert out["a"]["verdict"] == "unparsed", out


# ── Половина 2: кто попадает в числитель датчика ─────────────────────────────

def _cfg(db, window: int = WINDOW) -> None:
    db.execute("INSERT INTO system_config (key, value_num, category, updated_at) "
               "VALUES ('questions.memory_window_days', ?, 'questions', datetime('now'))",
               (float(window),))


def _candidate(db, fact_id: int, age_days: int, active: int = 1) -> None:
    db.execute(
        "INSERT INTO memory_facts (id, subject, mem_class, value, source, active, "
        "                          confidence, valid_from, created_at, updated_at) "
        "VALUES (?, 'self', 'question', ?, 'conversation', ?, 0.8, "
        "        date('now', ?), datetime('now'), datetime('now'))",
        (fact_id, f"кандидат #{fact_id}", active, f"-{age_days} days"))


def _run(db):
    import integrity_tests as it
    it._warnings.clear()
    result = it.check_question_candidates_not_discarded()
    fired = [w for w in it._warnings if "не побывав у судьи" in w[0]]
    return result, fired


def test_unjudged_candidate_that_aged_out_is_a_loss(db):
    """Позитивный контроль: активный, без отпечатка, возраст между окном и двумя
    окнами — судья его не видел, вопрос не задан, и это ровно та потеря, ради
    которой датчик существует."""
    _cfg(db)
    _candidate(db, 901, age_days=WINDOW + 5)

    result, fired = _run(db)
    assert result["died_unjudged"] == 1, result
    assert fired, "потеря не названа"
    assert "901" in fired[0][1], f"в тексте нет id — находку не с чего начать разбирать: {fired}"


def test_judged_and_rejected_candidate_is_not_a_loss(db):
    """ГЛАВНОЕ свойство новой оси. Тот же возраст, то же отсутствие отпечатка —
    но кандидат снят с активных, потому что судья его посмотрел. Это работа
    фильтра, а не утечка, и датчик обязан молчать.

    Негативный контроль связки: если убрать ретайр из ветки отказа
    (`task_agent.promote_memory_questions`), этот тест позеленеет ложно — там
    кандидат останется active=1. Половина 1 этого файла стережёт ту сторону.
    """
    _cfg(db)
    _candidate(db, 902, age_days=WINDOW + 5, active=0)

    result, fired = _run(db)
    assert result["died_unjudged"] == 0, result
    assert not fired, f"работа фильтра засчитана как потеря: {fired}"


def test_lifted_candidate_is_not_a_loss(db):
    """Отпечаток в tasks — второй способ оставить след. Кандидат мог остаться
    активным (ретайр при подъёме не делается), и отличает его только отпечаток."""
    _cfg(db)
    _candidate(db, 903, age_days=WINDOW + 5)
    db.add_task("Какую дозу вы принимаете?", type="question",
                fingerprint="question:mem:903")

    result, fired = _run(db)
    assert result["died_unjudged"] == 0, result
    assert not fired, f"поднятый кандидат засчитан как потеря: {fired}"


def test_old_cohort_leaves_the_window_and_stops_ringing(db):
    """Поток, а не запас: кандидат старше двух окон выпадает из счёта.

    Это то самое свойство, из-за которого июльская когорта перестаёт звонить сама,
    без литерала с датой рождения механизма в коде (§9). Верхняя граница здесь
    проверена ПОВЕДЕНИЕМ, а не чтением исходника.
    """
    _cfg(db)
    _candidate(db, 904, age_days=2 * WINDOW + 10)

    result, fired = _run(db)
    assert result["died_unjudged"] == 0, result
    assert not fired, f"накопленный запас снова в числителе: {fired}"


def test_fresh_candidate_is_not_yet_a_loss(db):
    """Нижняя граница: кандидат внутри окна ещё может быть поднят сегодня ночью."""
    _cfg(db)
    _candidate(db, 905, age_days=WINDOW - 5)

    result, fired = _run(db)
    assert result["died_unjudged"] == 0, result
    assert not fired, f"живой кандидат объявлен потерянным: {fired}"


def test_loss_is_named_regardless_of_how_much_was_asked(db):
    """Отношение больше не решает.

    Прежнее правило молчало, пока `умерло <= задано`: одна настоящая потеря в
    неделю, где задано двадцать вопросов, была невидима по построению. Здесь
    задано много, потеряно одно — и датчик обязан назвать его.
    """
    _cfg(db)
    _candidate(db, 906, age_days=WINDOW + 2)
    for i in range(20):
        db.add_task(f"вопрос {i}", type="question")

    result, fired = _run(db)
    assert result["died_unjudged"] == 1, result
    assert fired, "потеря утонула в числе заданных — ось осталась прежней"
