"""Характеризация owner_nag: окно 08–20 (Дом А, тестируемо кодом), консолидация
N→1, пульс §14 пишется всегда, via='none' = мёртвый колокол. Реальный Telegram
не дёргается (notify_operator замокан); стор и квитанция уведены в tmp."""
import datetime as _dt
import json
from datetime import date, datetime

import pytest

import owner_nag as on
import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock

TZ = on.HOME_TZ


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    monkeypatch.setenv("HEALTH_NAG_RECEIPT", str(tmp_path / "nag.json"))
    # Ночной цикл на тестовый день уже отработал: звонок не ждёт его (23.09). Ожидание
    # цикла — отдельный предмет, ниже.
    cycle = tmp_path / "cycle.json"
    cycle.write_text(json.dumps({"ran_at": "2026-08-02T08:00:40"}), encoding="utf-8")
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(cycle))
    (tmp_path / "secrets").mkdir()
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path / "secrets"))
    set_test_clock("2026-08-02")
    import notify  # ленивый: env секретов уже задан выше
    calls = []
    monkeypatch.setattr(notify, "notify_operator",
                        lambda msg, fallback=True: calls.append(msg) or "telegram")
    try:
        yield calls
    finally:
        clear_test_clock()


def _at(h):
    return datetime(2026, 8, 2, h, 0, tzinfo=TZ)


def test_rings_in_window_with_open(env):
    pd.park("g", "owner_decision", "сломанный оракул test_clone")
    r = on.run(now=_at(9))
    assert r["rang"] is True and r["via"] == "telegram"
    assert len(env) == 1 and "test_clone" in env[0]


def test_consolidates_many_into_one(env):
    pd.park("a", "owner_decision", "фикс А")
    pd.park("b", "plan_approval", "план Б")
    on.run(now=_at(11))
    assert len(env) == 1, "N гейтов — ОДИН звонок, не N"
    assert "Ждут твоего решения: 2" in env[0]


def test_bell_names_each_decision(env):
    """Решение владельца 28.09: звонок называет КАЖДОЕ решение, а не «на столе N».
    Негативный контроль на возврат прежней формы: пропадёт предмет — покраснеет."""
    pd.park("a", "owner_decision", "фикс А\nподробности для сессии")
    pd.park("b", "plan_approval", "план Б")
    on.run(now=_at(11))
    assert "1. фикс А" in env[0] and "2. план Б" in env[0], env[0]
    assert "подробности для сессии" not in env[0], "в звонок — только первая строка"
    assert "разбери решения" in env[0] and "docs/" not in env[0] and "консол" not in env[0]


def test_stalled_thread_is_named_by_its_title(env, tmp_path, monkeypatch):
    """Нить называется человеческим названием из INDEX, а не слагом."""
    monkeypatch.setattr(on, "_thread_title",
                        lambda slug: "Репетиция выгрузки" if slug == "export-rehearsal" else None)
    pd.park("thread-stalled:export-rehearsal", "owner_decision",
            "export-rehearsal без движения 9 дней — добить или закрыть?")
    on.run(now=_at(11))
    assert "Работа «Репетиция выгрузки»" in env[0] and "Доделать или закрыть?" in env[0], env[0]


def test_rings_once_a_day(env):
    """Раз в день (28.09): второй запуск в тот же день пишет пульс, но не звонит."""
    pd.park("g", "owner_decision", "висит")
    assert on.run(now=_at(8))["rang"] is True
    assert on.run(now=_at(11))["rang"] is False
    assert len(env) == 1
    set_test_clock("2026-08-03")
    assert on.run(now=datetime(2026, 8, 3, 11, 0, tzinfo=TZ))["rang"] is True
    assert len(env) == 2


def test_bell_names_nearest_silence_deadline(env):
    """Отсчёт — причина идти к столу: промолчишь — решится так-то."""
    pd.park("g", "owner_decision", "мелкий фикс",
            default="применить патч", rollback="git revert <sha>",
            auto_after=_dt.date(2026, 8, 16), executor="fix")
    on.run(now=_at(11))
    assert "16.08 одно решится само" in env[0] and "применить патч" in env[0], env[0]


def test_bell_survives_unreadable_created(env, monkeypatch):
    """Нечитаемая дата не роняет звонок: колокол важнее его украшений."""
    pd.park("g", "owner_decision", "висит")
    monkeypatch.setattr(pd, "list_open",
                        lambda today=None: [{"id": "g", "kind": "owner_decision",
                                             "summary": "висит", "created": "не-дата"}])
    on.run(now=_at(11))
    assert len(env) == 1 and "Ждут твоего решения: 1" in env[0]


@pytest.mark.parametrize("h", [7, 21, 3])
def test_silent_outside_window(env, h):
    pd.park("g", "owner_decision", "висит")
    r = on.run(now=_at(h))
    assert r["rang"] is False, f"{h}:00 вне окна — молчать"
    assert len(env) == 0, "вне окна notify_operator не зовём"


@pytest.mark.parametrize("h", [8, 20])
def test_window_boundaries_ring(env, h):
    pd.park("g", "owner_decision", "висит")
    assert on.run(now=_at(h))["rang"] is True, f"{h}:00 — граница окна, звоним"


def test_silent_when_nothing_open(env):
    r = on.run(now=_at(12))
    assert r["rang"] is False and len(env) == 0


def test_receipt_written_even_when_silent(env, tmp_path):
    """Пульс §14: квитанция пишется ВСЕГДА, иначе 'не звонил' и 'сдох' не различить."""
    on.run(now=_at(3))  # молчит
    rec = json.loads((tmp_path / "nag.json").read_text(encoding="utf-8"))
    assert rec["rang"] is False and "ran_at" in rec and rec["open_count"] == 0


def test_via_none_is_recorded_dead_doorbell(env, monkeypatch):
    """Оба канала легли → via='none' в квитанции: сигнал мёртвого колокола."""
    import notify
    monkeypatch.setattr(notify, "notify_operator", lambda msg, fallback=True: "none")
    pd.park("g", "owner_decision", "висит")
    r = on.run(now=_at(10))
    assert r["rang"] is True and r["via"] == "none"


def test_cli_run_leaves_fresh_pulse(tmp_path):
    """Регресс (баг 214д, живой smoke 2026-08-03): запуск owner_nag.py — точки входа
    launchd — оставляет квитанцию с СЕГОДНЯШНЕЙ датой, не затёртую селф-тестом с иной
    датой. До фикса __main__ звал run(now=2026-01-01) вторым, отравляя пульс."""
    import os
    import subprocess
    import sys
    import json as _j
    import datetime as _dt
    import owner_nag as on

    repo = os.path.dirname(os.path.abspath(on.__file__))
    receipt = tmp_path / "nag.json"
    (tmp_path / "s").mkdir()
    env = dict(os.environ)
    env.update(HEALTH_NAG_RECEIPT=str(receipt),
               HEALTH_PARKED_DB=str(tmp_path / "p.json"),
               HEALTH_SECRETS_DIR=str(tmp_path / "s"),
               PYTHONPATH=repo)
    subprocess.run([sys.executable, "owner_nag.py"], cwd=repo, env=env,
                   check=True, capture_output=True, timeout=30)
    rec = _j.loads(receipt.read_text(encoding="utf-8"))
    assert rec["ran_at"][:10] == _dt.date.today().isoformat(), \
        f"квитанция не сегодняшняя ({rec['ran_at']}) — __main__ затирает боевой пульс?"



# ── Колокол ждёт ночной цикл (23.09) ─────────────────────────────────────────
# Плисты — из tests/conftest.py: цикл 08:00, колокол 8/11/14/17/20.

@pytest.mark.owner_data
def test_bell_waits_for_the_cycle_at_its_minute(env, tmp_path):
    """08:00: цикл ещё не отчитался → колокол молчит (позвонит цикл), квитанция есть.
    Случай 23.09: звонок насчитал 14, а через 40 секунд цикл снял три."""
    (tmp_path / "cycle.json").write_text(json.dumps({"ran_at": "2026-08-01T08:00:40"}),
                                         encoding="utf-8")
    pd.park("g", "owner_decision", "висит")
    r = on.run(now=_at(8))
    assert r["rang"] is False and r.get("waiting_for") == "night_cycle" and env == []
    assert json.loads((tmp_path / "nag.json").read_text(encoding="utf-8"))["rang"] is False


def test_dead_cycle_does_not_silence_the_later_bell(env, tmp_path):
    """11:00: цикл так и не отчитался — звонок всё равно идёт (мёртвый цикл не глушит)."""
    (tmp_path / "cycle.json").write_text(json.dumps({"ran_at": "2026-08-01T08:00:40"}),
                                         encoding="utf-8")
    pd.park("g", "owner_decision", "висит")
    assert on.run(now=_at(11))["rang"] is True


def test_cycle_rings_once_after_it_finishes(env, tmp_path):
    """Цикл звонит сам после работы — и не второй раз, если колокол уже звонил."""
    pd.park("g", "owner_decision", "висит")
    r1 = on.ring_after_cycle(now=datetime(2026, 8, 2, 8, 1, tzinfo=TZ))
    r2 = on.ring_after_cycle(now=datetime(2026, 8, 2, 8, 2, tzinfo=TZ))
    assert r1["rang"] is True and r2["rang"] is False and len(env) == 1


# ── Колокол зовёт владельца только к его решениям (23.09) ──────────────────────
# Решение владельца «да»: инженерное со стола — вон. До 23.09 «anyio 4.13.0» стояло в
# одном счёте звонка с вопросами о здоровье. Инженерная очередь не пропадает: её счёт и
# возраст едут строкой понедельничного дайджеста (engineering_queue_line).

def test_engineering_card_alone_does_not_ring(env):
    pd.park("g", "dev_fix", "anyio 4.13.0 → 4.14.2")
    r = on.run(now=_at(9))
    assert r["rang"] is False and r["open_count"] == 0 and env == []


def test_engineering_card_is_not_counted_next_to_owner_questions(env):
    pd.park("dev", "dev_fix", "инженерное")
    pd.park("q", "owner_decision", "вопрос владельцу")
    on.run(now=_at(9))
    assert len(env) == 1 and "Ждут твоего решения: 1" in env[0] and "инженерное" not in env[0]


def test_engineering_queue_line_counts_only_engineering_and_names_oldest():
    today = date(2026, 9, 23)
    gates = [{"kind": "dev_fix", "created": "2026-09-21"},
             {"kind": "dev_fix", "created": "2026-09-01"},
             {"kind": "owner_decision", "created": "2026-08-01"}]
    line = on.engineering_queue_line(gates, today)
    assert "исправлений в работе: 2" in line and "ждёт 22 дня" in line, line
    assert on.engineering_queue_line([{"kind": "owner_decision"}], today) is None


def test_bell_line_keeps_the_question_and_never_cuts_a_word():
    """Холодное чтение 28.09: строка звонка резалась по символу («слишком близко к гр…»),
    и главное сообщение дня не говорило, о чём решение. Длинный абзац → первая фраза и
    вопрос карточки; слово не режется."""
    import owner_nag
    long = ("Выдуманная находка " + "очень " * 30 + "длинная. Вторая фраза. Что делать?"
            "\n\n• Вариант — цена")
    line = on._headline(long)
    assert line.endswith("Что делать?") and line.startswith("Выдуманная находка")
    one = "Одна фраза без точки " + "слово " * 80
    cut = on._headline(one)
    assert cut.endswith(" …") and not cut[:-2].endswith("сл")
    assert on._headline("Коротко. Что делать?\n\n• A") == "Коротко. Что делать?"


# ── 30.09: строка звонка не состоит из рамки ────────────────────────────────────
# Дословно первые абзацы двух карточек, по которым 30.09 звонок сказал владельцу только
# «это про систему, срочность средняя» — и ни слова о предмете.
_REAL_0930 = [
    "Это про систему, не про твоё здоровье. Срочность средняя — угрозы прямо сейчас нет, "
    "но тянуть дольше недели не стоит.\n\nВ твоей системе три новые автоматические задачи. "
    "Они уже работают, но нигде не записаны.\n\nЧто хочешь сделать с этими тремя задачами?",
    "Это про работу системы, не про чьё-либо здоровье напрямую — но срочность средняя: чем "
    "дольше ждать, тем дольше данные лаборатории партнёра обрабатываются по старым правилам."
    "\n\nЧасть автоматических задач продолжает работать по старым правилам.\n\nЧто делать?",
]


_REAL_0930 += [
    # Контейнерный стол 30.09, строки 4 и 6 звонка: рамка иной формы.
    "Про твою систему мониторинга здоровья, не срочно — можно решить в течение нескольких "
    "дней.\n\nТри задачи по расписанию работают без копии.\n\nЧто делать?",
    "Несрочно — можно решить в течение нескольких дней.\n\nТри задачи по расписанию "
    "работают без копии.\n\nЧто делать?",
]


@pytest.mark.parametrize("summary", _REAL_0930)
def test_bell_line_skips_the_frame(summary):
    line = on._headline(summary)
    assert not on._FRAME.match(line), line
    assert "задач" in line, line


def test_bell_line_keeps_a_card_without_frame():
    """Негативный контроль: карточка без рамки не теряет строку."""
    s = "Три задачи по расписанию работают без копии в хранилище кода.\n\nЧто делать?"
    assert on._headline(s) == "Три задачи по расписанию работают без копии в хранилище кода."


def test_handwritten_intake_card_names_its_subject():
    import i18n
    line = on._headline(i18n.t("owner.card.intake", url="x"))
    assert "документ" in line and not line.startswith("Про здоровье"), line



def test_forgotten_desk_rings_one_line_not_the_list(env, monkeypatch):
    """desk-guard 30.09: стол забыл карточки — вместо списка одна строка о потере памяти,
    и звонит даже при пустом столе (после переезда он пуст). Решённые вопросы не шлются."""
    pd.park("g", "owner_decision", "UC-B-09: новое необоснованное r=0.42")
    monkeypatch.setattr(pd, "_remembered", lambda: 39)
    r = on.run(now=_at(9))
    assert r["rang"] is True and r["desk_memory_lost"] == [1, 39]
    assert len(env) == 1 and "потерял память" in env[0] and "r=0.42" not in env[0]
    on.run(now=_at(11))
    assert len(env) == 1, "раз в день, как обычный звонок"


def test_desk_memory_only_grows_and_ignores_foreign_store(env, monkeypatch):
    """Отметка в базе — только вверх; стол, уведённый env (тест/стенд), с базой не судится."""
    assert pd._remembered() is None, "HEALTH_PARKED_DB задан — судить нечем"
    written = []
    monkeypatch.setattr(pd, "_remembered", lambda: 1)
    import config_db
    monkeypatch.setattr(config_db, "upsert_config", lambda k, **kw: written.append((k, kw["value_num"])))
    pd.park("a", "dev_fix", "x"); pd.park("b", "dev_fix", "y")
    pd.remember_size()
    assert written == [(pd.MEMORY_KEY, 2)]
    monkeypatch.setattr(pd, "_remembered", lambda: 5)
    pd.remember_size()
    assert written == [(pd.MEMORY_KEY, 2)], "вниз не пишем"
