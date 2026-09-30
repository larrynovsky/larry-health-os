"""Хвосты нити question-answer-channel: потолок производства, дедуп после судьи,
улика «система уже измеряла».

Что здесь стережётся и почему именно так:

* Потолок — тест зовёт подъём с судьёй, который ВЗРЫВАЕТСЯ при вызове. Зелёный
  возможен только если до судьи не дошло. Снимешь потолок — тест падает с
  исключением, а не с мягким assert: оракул причинён гейтом (§20).
* Дедуп — два разных факта, одна формулировка судьи. Без дедупа родятся две задачи.
* Улика — ТРИ теста, и главный из них негативный контроль: фильтр обязан пропускать
  «согласовали ли дозу железа с врачом» НАСКВОЗЬ, хотя измерения железа есть.
  Без этого теста фильтр может задушить канал и остаться зелёным — ровно тот класс,
  которым канал болел до 12.09 (тишина невидима снаружи).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lab_canon  # noqa: E402


# ── Чистая часть: какие аналиты упомянуты в тексте (без БД) ──────────────────

@pytest.mark.parametrize("text, expected", [
    ("Какие значения HbA1c были в начале года?", {"HbA1c"}),
    ("Когда были сданы последние анализы холестерина и ApoB?", {"ApoB", "Cholesterol_Total"}),
    ("Когда запланирована ваша следующая проверка ферритина?", {"Ferritin"}),
    # косвенный падеж имени на гласную: «железо» → «железа». Прежнее правило
    # (хвост букв) такое не ловило, и негативный контроль ниже был бы ложно-зелёным.
    ("Вы согласовали дозировку препарата железа с врачом?", {"Iron"}),
])
def test_analytes_mentioned_finds_names(text, expected):
    assert lab_canon.analytes_mentioned(text) == expected


@pytest.mark.parametrize("text", [
    "Как часто и в какое время вы обычно гуляете?",
    "Появились ли высыпания после контакта с герпесом?",
    "Какие ещё продукты вы едите в течение дня помимо завтрака?",
])
def test_analytes_mentioned_silent_on_non_lab_questions(text):
    assert lab_canon.analytes_mentioned(text) == set()


def test_analytes_mentioned_empty_input():
    assert lab_canon.analytes_mentioned("") == set()
    assert lab_canon.analytes_mentioned(None) == set()


# ── Улика для судьи ─────────────────────────────────────────────────────────

def test_evidence_names_measurements_that_exist(monkeypatch):
    import task_agent as ta
    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"HbA1c"})
    monkeypatch.setattr("labs_db.get_lab_series",
                        lambda name, **kw: [{"date": "2026-08-29", "value": 5.4}])
    ev = ta._measurements_evidence("Какие значения HbA1c были в начале года?")
    assert "HbA1c" in ev and "2026-08-29" in ev


def test_evidence_silent_when_no_measurements(monkeypatch):
    """Имя упомянуто, а данных нет — улики нет, и судья решает как раньше."""
    import task_agent as ta
    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"HbA1c"})
    monkeypatch.setattr("labs_db.get_lab_series", lambda name, **kw: [])
    assert ta._measurements_evidence("Какие значения HbA1c?") == ""


def test_evidence_is_fail_open_when_resolver_breaks(monkeypatch):
    """R1: сбой резолвера НЕ должен глушить вопрос. Зеркало doubt_resolves_into_a_question."""
    import task_agent as ta

    def boom(_):
        raise RuntimeError("резолвер лёг")

    monkeypatch.setattr(lab_canon, "analytes_mentioned", boom)
    assert ta._measurements_evidence("Какие значения HbA1c?") == ""


def test_negative_control_evidence_does_not_decide(monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ R1. «Согласовали ли дозу железа с врачом» — измерения
    железа ЕСТЬ, улика соберётся, и это НЕ повод не спрашивать: договорённость с
    врачом есть только у человека.

    Тест стережёт границу ответственности: `_measurements_evidence` возвращает
    УЛИКУ, а не вердикт. Если кто-то превратит её в предикат «не спрашивать»,
    вопросы про договорённости начнут гаснуть — и снаружи это будет невидимо.
    """
    import task_agent as ta
    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"Iron"})
    monkeypatch.setattr("labs_db.get_lab_series",
                        lambda name, **kw: [{"date": "2026-08-29", "value": 12.0}])
    ev = ta._measurements_evidence("Вы согласовали дозировку железа с врачом?")
    assert isinstance(ev, str) and "Iron" in ev, "улика должна собраться"
    # и она именно строка-улика, а не решение: булева/None означали бы предикат
    assert ev is not True and ev is not False


# ── Потолок производства ────────────────────────────────────────────────────

def _judge_that_must_not_be_called(items):
    raise AssertionError(
        "судья вызван при полной очереди — потолок производства снят или обойдён")


def test_ceiling_stops_promotion_before_the_judge(monkeypatch):
    """Очередь не меньше дневного бюджета → подъём пуст, судья не вызван.

    Судья взрывается при вызове намеренно: зелёный тест доказывает, что до него не
    дошли, а не что он вернул пусто."""
    import task_agent as ta
    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 2}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery",
                        lambda: [{"id": 1}, {"id": 2}, {"id": 3}])
    monkeypatch.setattr(ta, "addressed_to_patient", _judge_that_must_not_be_called)
    assert ta.promote_memory_questions() == []


def test_ceiling_absent_config_is_fail_closed(monkeypatch):
    """Нет questions.max_per_day → не поднимаем ничего, как и доставка."""
    import task_agent as ta
    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr(ta, "addressed_to_patient", _judge_that_must_not_be_called)
    assert ta.promote_memory_questions() == []


def test_room_in_queue_lets_the_judge_work(monkeypatch):
    """Обратная сторона: потолок, закрытый наглухо, тоже дефект.

    Очередь пуста → судья ОБЯЗАН быть вызван. Без этого теста можно «починить»
    производство, просто выключив его, и все остальные тесты останутся зелёными."""
    import task_agent as ta
    called = {}

    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 2}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr("memory_facts_db.get_facts",
                        lambda cls, since_days=None: [{"id": 901, "value": "Какую дозу?", "source": "conversation"}])
    monkeypatch.setattr("health_db.get_open_questions", lambda: [])
    monkeypatch.setattr("health_db.save_task", lambda **kw: 555)

    def judge(items):
        called["yes"] = True
        return {"901": {"verdict": "patient", "reason": "", "ru": "Какую дозу вы принимаете?"}}

    monkeypatch.setattr(ta, "addressed_to_patient", judge)
    _patch_taken_empty(monkeypatch)
    created = ta.promote_memory_questions()
    assert called.get("yes"), "судья не вызван при свободной очереди"
    assert [c["id"] for c in created] == [555]


# ── Дедуп после судьи ───────────────────────────────────────────────────────

def _patch_taken_empty(monkeypatch):
    """Отбор «уже поднятых» читает tasks напрямую через get_conn — подменяем на пусто."""
    import health_db

    class _Conn:
        def execute(self, *a, **kw):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(health_db, "get_conn", lambda *a, **kw: _Conn())


def test_duplicate_formulation_creates_one_task(monkeypatch):
    """Два независимо придуманных вопроса о времени напоминания дают одну формулировку судьи и одну задачу. Сравнение исходных фактов не заменяет дедуп результата."""
    import task_agent as ta
    saved = []

    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 5}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr("memory_facts_db.get_facts", lambda cls, since_days=None: [
        {"id": 4101, "value": "Has the user chosen a time for the reminder?", "source": "conversation"},
        {"id": 4102, "value": "Which reminder time did the user select?", "source": "conversation"},
    ])
    monkeypatch.setattr("health_db.get_open_questions", lambda: [])
    monkeypatch.setattr("memory_facts_db.retire_fact", lambda fid, reason: True)

    def save(**kw):
        saved.append(kw["content"])
        return 600 + len(saved)

    monkeypatch.setattr("health_db.save_task", save)
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        "4101": {"verdict": "patient", "reason": "", "ru": "Вы выбрали время напоминания?"},
        "4102": {"verdict": "patient", "reason": "", "ru": "Вы выбрали время напоминания?"},
    })
    _patch_taken_empty(monkeypatch)

    created = ta.promote_memory_questions()
    assert len(saved) == 1, f"дубль прошёл: {saved}"
    assert len(created) == 1



def test_duplicate_of_already_open_question_is_not_created(monkeypatch):
    """Дубль уже ОТКРЫТОГО вопроса тоже не рождается — сравниваем с живой очередью,
    а не только внутри партии."""
    import task_agent as ta
    saved = []

    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 5}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr("memory_facts_db.get_facts", lambda cls, since_days=None: [
        {"id": 4102, "value": "Has a reminder time been selected?", "source": "conversation"},
    ])
    monkeypatch.setattr("health_db.get_open_questions",
                        lambda: [{"content": "Вы выбрали время напоминания?"}])
    monkeypatch.setattr("memory_facts_db.retire_fact", lambda fid, reason: True)
    monkeypatch.setattr("health_db.save_task", lambda **kw: saved.append(kw) or 1)
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        "4102": {"verdict": "patient", "reason": "", "ru": "Вы выбрали время напоминания?"},
    })
    _patch_taken_empty(monkeypatch)

    assert ta.promote_memory_questions() == []
    assert saved == []



def test_different_questions_are_not_glued(monkeypatch):
    """R3: похожие, но РАЗНЫЕ вопросы обязаны родиться оба. Потерянный вопрос
    невидим, лишний стоит одного сообщения — сравнение строгое, не пороговое."""
    import task_agent as ta
    saved = []

    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None: {"questions.memory_window_days": 45,
                                                 "questions.promote_batch": 5,
                                                 "questions.max_per_day": 5}.get(k, default))
    monkeypatch.setattr("health_db.get_questions_needing_delivery", lambda: [])
    monkeypatch.setattr("memory_facts_db.get_facts", lambda cls, since_days=None: [
        {"id": 103, "value": "magnesium dose", "source": "conversation"},
        {"id": 101, "value": "vitamin D dose", "source": "conversation"},
    ])
    monkeypatch.setattr("health_db.get_open_questions", lambda: [])
    monkeypatch.setattr("memory_facts_db.retire_fact", lambda fid, reason: True)
    monkeypatch.setattr("health_db.save_task",
                        lambda **kw: saved.append(kw["content"]) or (700 + len(saved)))
    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        "103": {"verdict": "patient", "reason": "", "ru": "Какую дозу магния вы принимаете?"},
        "101": {"verdict": "patient", "reason": "", "ru": "Какую дозу витамина D вы принимаете?"},
    })
    _patch_taken_empty(monkeypatch)

    ta.promote_memory_questions()
    assert len(saved) == 2, f"склеены разные вопросы: {saved}"


# ── Датчик выброшенных кандидатов судит ПОТОК, а не запас ────────────────────

def test_discarded_candidates_sensor_reads_flow_not_stock():
    """Датчик, считающий ВСЕХ когда-либо умерших кандидатов, растёт монотонно: раз
    перевалив за число заданных, он краснеет каждую ночь независимо от текущего
    поведения канала — и его учатся пролистывать (риск R2 плана нити).

    Тест судит ФОРМУ запроса, потому что судить поведение пришлось бы на живой базе
    с историей в месяцы: у предиката обязана быть ВЕРХНЯЯ граница возраста, а не
    только нижняя. Ограничение названо: эта проверка ловит снятие границы, но не
    докажет, что окно выбрано верно.
    """
    import inspect

    import integrity_tests as it
    src = inspect.getsource(it.check_question_candidates_not_discarded)
    body = src.split('"""')[-1]          # без докстринга: он про границу и говорит
    # Проверяем ИМЕННО возраст кандидата (valid_from). Первая версия этого теста
    # искала голое «<= ?» и была ложно-зелёной: та же подстрока стоит в соседнем
    # запросе про created_at, и мутация «снять верхнюю границу» его не роняла.
    # Поймано мутацией, а не рассуждением.
    upper = "julianday('now') - julianday(valid_from) <= ?"
    assert upper in body, (
        "у предиката «умер незаданным» нет верхней границы ВОЗРАСТА КАНДИДАТА — он "
        "считает накопленный запас, а не поток за окно, и будет краснеть вечно")


# ── Разбор ответа судьи: сломанный объект не убивает партию ──────────────────

_BROKEN = Path(__file__).resolve().parents[1] / "fixtures" / "judge_broken_object_2026-09-13.json"


def test_broken_object_in_the_middle_does_not_kill_the_batch():
    """НАСТОЯЩИЙ ответ судьи от 13.09, а не сочинённый: модель закрыла объект до
    поля "ru", дальше пошёл осиротевший «"ru": …}», а массив закончился нормальной
    «]». Прежний salvage резал «до последней }» и на таком ответе падал целиком.

    Замер, ради которого это чинилось: 2 сбоя разбора на 10 прогонов судьи. Каждый
    сбой в extract_tasks_from_report понижает ВСЕ вопросы отчёта в action, и это
    необратимо — задача создана, вопрос не задан никогда.
    """
    import task_agent as ta
    raw = _BROKEN.read_text(encoding="utf-8")
    out = ta._parse_tasks_json(raw)
    ids = [o.get("id") for o in out]
    assert len(out) >= 6, f"спасено слишком мало объектов: {ids}"
    assert "a1" in ids and "p3" in ids, (
        f"потеряны соседи сломанного объекта — salvage режет партию, а не элемент: {ids}")


def test_truncated_tail_still_salvaged():
    """Старый способ сломаться (обрыв по max_tokens) обязан лечиться тем же
    механизмом — иначе починка одного случая тихо отменила бы починку другого."""
    import task_agent as ta
    raw = '[{"id": "a", "verdict": "patient"}, {"id": "b", "verdict": "unsu'
    out = ta._parse_tasks_json(raw)
    assert [o["id"] for o in out] == ["a"]


def test_total_garbage_still_raises():
    """Fail-closed остался: ответ, в котором НЕТ ни одного объекта, — это не
    «кривая строка», а «модель отвечает не о том», и молчать о таком нельзя."""
    import json as _json

    import task_agent as ta
    with pytest.raises(_json.JSONDecodeError):
        ta._parse_tasks_json("извини, не могу помочь с этим запросом")


def test_braces_inside_strings_do_not_break_the_scan():
    """Текст вердикта может содержать фигурные скобки; сканер обязан считать их
    частью строки, а не границей объекта."""
    import task_agent as ta
    raw = '[{"id": "a", "reason": "формат {id} сломан"}, СЛОМАНО]'
    out = ta._parse_tasks_json(raw)
    assert out[0]["reason"] == "формат {id} сломан"


# ── Улика не несёт скрытого окна (Д1, 13.09) ────────────────────────────────

def test_evidence_sees_measurements_older_than_two_years(monkeypatch):
    """Запрос улики должен читать всю историю, включая синтетическую строку за пределами окна по умолчанию."""
    import task_agent as ta
    seen = {}

    def series(name, n_days=730, **kw):
        seen["n_days"] = n_days
        return [{"date": "2015-02-12", "value": 13.7}] if n_days > 1200 else []

    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"Ferritin"})
    monkeypatch.setattr("labs_db.get_lab_series", series)
    ev = ta._measurements_evidence("Какой сейчас уровень ферритина?")
    assert "Ferritin" in ev and "2015-02-12" in ev, (
        f"улика не увидела измерение старше окна (n_days={seen.get('n_days')})")



def test_evidence_counts_whole_history_not_window(monkeypatch):
    """Синтетический длинный ряд считается полностью, а не только внутри окна."""
    import task_agent as ta

    def series(name, n_days=730, **kw):
        rows = [{"date": f"201{i}-02-12"} for i in range(7)]
        return rows if n_days > 1200 else rows[:3]

    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"Albumin"})
    monkeypatch.setattr("labs_db.get_lab_series", series)
    assert "7 знач." in ta._measurements_evidence("Какие значения Albumin?")



def test_evidence_stays_short_on_long_history(monkeypatch):
    """R1: улика печатает счёт и дату, а не список — длина не растёт с историей."""
    import task_agent as ta
    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"Cholesterol_Total"})
    monkeypatch.setattr("labs_db.get_lab_series",
                        lambda n, n_days=730, **kw: [{"date": "2026-08-29"}] * 200)
    ev = ta._measurements_evidence("Какой холестерин?")
    assert len(ev) < 120, f"улика раздулась: {len(ev)} симв."


def test_evidence_empty_when_analyte_never_measured(monkeypatch):
    """Граница не съехала в другую сторону: нет строк — нет улики."""
    import task_agent as ta
    monkeypatch.setattr(lab_canon, "analytes_mentioned", lambda t: {"Insulin"})
    monkeypatch.setattr("labs_db.get_lab_series", lambda n, n_days=730, **kw: [])
    assert ta._measurements_evidence("Какой инсулин?") == ""


# ── Исход разбора виден снаружи (Д2, 13.09) ─────────────────────────────────

def _capture_stats(monkeypatch):
    store = {}
    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None, conn=None: store.get(k, default))
    monkeypatch.setattr("config_db.upsert_config",
                        lambda k, value_text=None, value_num=None, value_json=None,
                        category=None, source=None: store.__setitem__(k, value_json))
    return store


def _totals(st):
    """Сумма по всем источникам. Плоских итогов в счётчике НЕТ сознательно: итог и
    разбивка — два дома одного числа, и они разъезжаются молча."""
    out = {"ok": 0, "partial": 0, "failed": 0}
    for c in (st.get("by") or {}).values():
        for k in out:
            out[k] += int(c.get(k) or 0)
    return out


def test_full_failure_is_counted_as_failed(monkeypatch):
    """Полный отказ разбора обязан попасть в счётчик — иначе он невидим, а именно
    он необратимо понижает вопросы отчёта в action."""
    import json as _json

    import task_agent as ta
    store = _capture_stats(monkeypatch)
    with pytest.raises(_json.JSONDecodeError):
        ta._parse_tasks_json("извини, не могу помочь")
    t = _totals(store[ta.PARSE_STATS_KEY])
    assert t["failed"] == 1 and t["partial"] == 0


def test_partial_parse_is_counted_separately(monkeypatch):
    """Частичный разбор считается ОТДЕЛЬНО от отказа: он не поломка системы, и
    датчик на нём не краснеет."""
    import task_agent as ta
    store = _capture_stats(monkeypatch)
    ta._parse_tasks_json(_BROKEN.read_text(encoding="utf-8"))
    t = _totals(store[ta.PARSE_STATS_KEY])
    assert t["partial"] == 1 and t["failed"] == 0


def test_stats_reset_on_new_day(monkeypatch):
    """Счёт за сутки, а не накопленный: иначе число однажды перевалит порог и
    датчик будет краснеть вечно (вчерашний урок про запас и поток)."""
    import task_agent as ta
    store = _capture_stats(monkeypatch)
    store[ta.PARSE_STATS_KEY] = {"date": "1999-01-01",
                                 "by": {"старое": {"ok": 7, "partial": 3, "failed": 2}}}
    ta._parse_tasks_json('[{"id": "a"}]')
    st = store[ta.PARSE_STATS_KEY]
    t = _totals(st)
    assert t["ok"] == 1 and t["failed"] == 0 and st["date"] != "1999-01-01"
    assert "старое" not in st["by"], "вчерашние источники обязаны уйти вместе с датой"


def test_stats_failure_does_not_break_parsing(monkeypatch):
    """Счётчик — best-effort: недоступная БД не должна ронять разбор."""
    import task_agent as ta

    def boom(*a, **kw):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr("config_db.get_config", boom)
    assert ta._parse_tasks_json('[{"id": "a"}]') == [{"id": "a"}]


# ── Провенанс счётчика: чей отказ (13.09, второй заход) ─────────────────────

def test_source_is_recorded_as_its_own_bucket(monkeypatch):
    """Кто звал разбор — записано. Без этого 13.09 счётчик показал 28 поломок из 45,
    а логи обоих ботов за те же сутки — ноль: в одном ведре лежали бот и мои пробы."""
    import task_agent as ta
    store = _capture_stats(monkeypatch)
    monkeypatch.setattr(ta, "_parse_source", lambda: "plans/probe_x.py")
    ta._parse_tasks_json('[{"id": "a"}]')
    assert list(store[ta.PARSE_STATS_KEY]["by"]) == ["plans/probe_x.py"]


def test_two_sources_do_not_overwrite_each_other(monkeypatch):
    """Второй источник не затирает первый: иначе разбивка врала бы ровно в тот день,
    когда рядом с ботом работает разработчик — то есть в день расследования."""
    import task_agent as ta
    store = _capture_stats(monkeypatch)
    monkeypatch.setattr(ta, "_parse_source", lambda: "task_agent.py")
    ta._parse_tasks_json('[{"id": "a"}]')
    monkeypatch.setattr(ta, "_parse_source", lambda: "plans/probe_x.py")
    ta._parse_tasks_json('[{"id": "b"}]')
    by = store[ta.PARSE_STATS_KEY]["by"]
    assert by["task_agent.py"]["ok"] == 1 and by["plans/probe_x.py"]["ok"] == 1


def test_source_of_a_repo_file_is_a_repo_relative_path(monkeypatch):
    """Источник внутри репозитория записывается ПУТЁМ: только по пути читатель может
    спросить периметр «оснастка или рабочий код». Имя файла такого права не даёт."""
    import sys as _sys
    from pathlib import Path as _P

    import task_agent as ta
    root = _P(ta.__file__).resolve().parent
    monkeypatch.setattr(_sys, "argv", [str(root / "plans" / "probe_x.py")])
    assert ta._parse_source() == "plans/probe_x.py"


def test_source_outside_the_repo_degrades_to_a_name(monkeypatch):
    """Запуск вне репозитория не роняет счётчик — остаётся имя. Датчик такой источник
    считает продовым (fail-loud), и это проверяет отдельный тест ниже."""
    import sys as _sys

    import task_agent as ta
    monkeypatch.setattr(_sys, "argv", ["/usr/bin/somewhere/else.py"])
    assert ta._parse_source() == "else.py"


def test_test_run_does_not_write_the_production_counter():
    """⭐ Оракул глушителя: прогон тестов не пишет боевой счётчик разбора.

    14.09 ночной прогон доложил владельцу «3 полн. отказ(ов) … громче всех
    «__main__.py»» при нуле продовых отказов: ведро насыпал сам pytest
    (`run_checks.sh` зовёт `$PY -m pytest`, argv[0] = …/pytest/__main__.py —
    путь вне репозитория, имя периметр не классифицирует, неизвестное = прод).
    Глушитель стоит в `tests/conftest.py::_no_real_parse_stats`; снимут его —
    покраснеет здесь, а не через сутки в телефоне владельца.

    Что проверено честно: глушитель на месте и запись боевого ключа до БД не
    доходит. Делегирование ЧУЖИХ ключей настоящему `config_db` здесь не
    проверяется (вызов ушёл бы в канон) — его держат тесты, которые пишут
    конфиг по своим ключам."""
    import health_db  # noqa: F401 — первым: config_db-первым даёт круговой импорт
    import config_db

    import task_agent as ta
    assert getattr(config_db.upsert_config, "muzzles_parse_stats", False), (
        "глушитель боевого счётчика разбора снят — прогон тестов снова насыплет "
        "ведро в llm_parse.stats и датчик закричит о продовых отказах, которых нет")
    assert config_db.upsert_config(
        ta.PARSE_STATS_KEY, value_json={"date": "1999-01-01", "by": {}},
        category="llm", source="oracle") is None


def _stats(by, date="2026-09-13"):
    return {"date": date, "by": by}


def _sensor(monkeypatch, by):
    """Прогнать датчик на подставном счётчике и вернуть (результат, список FAIL'ов).

    `fail_` не бросает, а КОПИТ — у ночного монитора один упавший датчик не должен
    обрывать остальные. Поэтому оракул смотрит на то, что записано, а не ловит
    исключение: `pytest.raises` здесь зеленел бы при любом поведении датчика."""
    import integrity_tests as it
    said = []
    monkeypatch.setattr(it, "fail_", lambda label, detail="": said.append(label))
    monkeypatch.setattr("config_db.get_config",
                        lambda k, default=None, conn=None:
                        _stats(by) if k == "llm_parse.stats" else default)
    return it.check_llm_answer_parsed(), said


def test_sensor_is_silent_on_harness_failures(monkeypatch):
    """⭐ Отказ в ПРОБЕ разработчика тревогу не поднимает. Ложный красный в общем
    ночном прогоне ядовит так же, как ложно-зелёный: после второго раза красное
    перестают читать."""
    res, said = _sensor(monkeypatch,
                        {"plans/probe_x.py": {"ok": 0, "partial": 3, "failed": 5}})
    assert said == [], f"датчик закричал от пробы разработчика: {said}"
    assert res["failed"] == 0 and res["оснастка"]["отказов"] == 5


def test_sensor_reds_on_production_failure(monkeypatch):
    """Позитивный контроль к предыдущему: продовый отказ обязан краснеть, иначе
    предыдущий тест зелен потому, что датчик не краснеет ни на чём."""
    _, said = _sensor(monkeypatch, {"task_agent.py": {"ok": 1, "partial": 0, "failed": 2}})
    assert said and "не разобран" in said[0]


def test_unknown_source_is_judged_as_production(monkeypatch):
    """Направление отказа выбрано: неизвестный источник считается ПРОДОВЫМ. Датчик,
    ошибающийся в сторону крика, чинится за минуту; ошибающийся в сторону тишины не
    чинится вовсе — о нём не узнают."""
    _, said = _sensor(monkeypatch, {"else.py": {"ok": 0, "partial": 0, "failed": 1}})
    assert said, "неизвестный источник промолчал — тишина выбрана не в ту сторону"


def test_broken_perimeter_does_not_silence_the_sensor(monkeypatch):
    """Недоступный периметр не превращает датчик в молчащий: всё судится как прод,
    и об этом отдельно предупреждают. Тихий отказ классификатора был бы дырой того
    же класса, что и весь этот хвост."""
    import integrity_tests as it
    monkeypatch.setattr(it, "_harness_classifier",
                        lambda: ((lambda s: False), "периметр не прочитан"))
    _, said = _sensor(monkeypatch,
                      {"plans/probe_x.py": {"ok": 0, "partial": 0, "failed": 1}})
    assert said, "сломанный периметр заглушил датчик"


def test_harness_classifier_asks_the_single_home_of_the_perimeter():
    """Классификатор — НЕ свой список каталогов, а вопрос к дому периметра (§15).
    Проверяем исполнением: реальные пути из репозитория разводятся верно."""
    import integrity_tests as it
    is_harness, err = it._harness_classifier()
    assert err is None, f"периметр не прочитан: {err}"
    assert is_harness("plans/probe_question_addressing_2026-09-12.py")
    assert is_harness("tests/unit/test_question_tails.py")
    assert not is_harness("task_agent.py")
