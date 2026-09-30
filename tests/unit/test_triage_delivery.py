#!/usr/bin/env python3.11
"""
test_triage_delivery — датчик ДОСТАВКИ warn-уровня (anti «детект без доставки»).

Проверяет, что triage форвардит пользователю ВСЕ варны, кроме явного mute-списка.
Тест на ДОСТАВКУ (что попадёт в needs_user), не на наличие функции.
Регресс-гард: если кто-то снова сузит фильтр до whitelist — этот тест покраснеет.

Чистая функция classify_warnings → без IO, без done-маркера, без сети.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import triage_agent as t


def test_non_whitelisted_warns_are_delivered():
    """Раньше survivorship/medications/гипотезы молча гибли. Теперь — доставка."""
    warnings = [
        ["survivorship: analyzer не запускался 22д", "последний 2026-06-07"],
        ["эпизоды лечения есть, а medications пуста", ""],
        ["нет запусков genome_update_agent", "ни разу не запускался?"],
    ]
    out = t.classify_warnings(warnings)
    assert len(out) == 3, f"ожидалось 3 доставки, получено {len(out)}: {out}"
    joined = "\n".join(out)
    assert "survivorship" in joined
    assert "medications" in joined
    assert "genome_update" in joined


def test_mute_list_is_dropped():
    """Неактонируемый шум не должен пинговать пользователя.

    Каждый mute-пункт — со своим кейсом (правило «mute = обоснование + тест»).
    """
    assert t.classify_warnings([["нет steps за 2д", "оба дня пусты"]]) == []
    assert t.classify_warnings([["нет активных протоколов", "возможно пока не созданы"]]) == [], \
        "«нет активных протоколов» должен быть в mute (ожидаемое состояние)"
    assert t.classify_warnings([["20 гипотез без активных экспериментов", "без движения"]]) == [], \
        "«без активных экспериментов» в mute (эксперименты свёрнуты, BL-EXP-1)"


def test_special_phrasing_preserved():
    """«анализы» и «клинич» сохраняют человеческую формулировку, не generic ⚠️."""
    out = t.classify_warnings([
        ["анализы 90д", "давно не сдавал"],
        ["клинический период истёк", "период истёк после визита"],
    ])
    assert any("Когда планируешь" in x for x in out)
    assert any("после визита к врачу" in x for x in out)


def test_tenant_tag_survives_rephrasing():
    """Тег тенанта должен сохраниться при переформулировке придуманного уведомления.

    Мутация «убрать who из формата» обязана уронить тест: доставка
    не должна менять субъекта утверждения или оставлять висящий дефис."""
    out = t.classify_warnings([["[health_partner] анализы устарели: 512д (порог 270д)", ""]])
    assert len(out) == 1
    assert "health_partner" in out[0], f"тенант потерян при переформулировке: {out[0]!r}"
    assert "— ." not in out[0], f"висящий дефис от пустого msg: {out[0]!r}"
    assert "512" in out[0]


def test_owner_analyses_have_no_tenant_tag():
    """Негативный контроль: у собственного warn тега нет — и приписывать его нельзя,
    иначе «есть тег» перестанет отличать чужое от своего."""
    out = t.classify_warnings([["анализы требуют обновления: 200д", "последний: 2025-10-10"]])
    assert "[" not in out[0], f"выдуманный тег у своего же warn: {out[0]!r}"


def test_mute_list_short_and_justified():
    """Mute-список должен оставаться коротким (анти-дыра Р2): растёт → ревью."""
    assert len(t.MUTE_WARN_SUBSTRINGS) <= 3, \
        "mute-список разрастается — каждый пункт нужен с обоснованием и тестом"


def test_fdr_gate_sensor_warns_are_delivered():
    """Датчики валид-гейта (лаб-реактивация Гр.5 + партнёр-готовность Гр.3 + MC-зазор Ф.1/3-а) —
    НЕ в mute, доходят до владельца Telegram'ом. Регресс-гард: заглушат имя → детект без доставки."""
    out = t.classify_warnings([
        ["Lab-путь готов к возврату в release-scope",
         "3 аналитов ≥ порога (нужно 3) — верни lab-путь ре-объявлением [сигнал от 2026-07-23]"],
        ["Партнёр [health_partner] готов к A/D-стратификации (Группа 3)",
         "400д daily_metrics ≥ 365 И 2 периодов ≥ 2 — эпохи есть [сигнал от 2026-07-23]"],
        ["MC-зазор: 1 открытий на разрешении Монте-Карло",
         "решение в ±2·MCSE от линии → триггер Фаз 1/3-а"],
        ["mc_gap датчик замолк: 9д (порог 8д)", "longitudinal не пишет артефакт"],
        ["pass-set замерцал: 1 семей(ья) сменили членство", "первое онлайн-прибытие → триггер §5"],
    ])
    assert len(out) == 5, f"ожидалось 5 доставок: {out}"
    joined = "\n".join(out)
    assert "Lab-путь" in joined and "Партнёр" in joined, f"датчики гейта не доставлены: {out}"
    assert "MC-зазор" in joined and "замолк" in joined, f"MC-зазор датчик не доставлен: {out}"
    assert "замерцал" in joined, f"датчик мерцания не доставлен: {out}"


def test_run_triage_guarded_journals_crash(monkeypatch, tmp_path, fault_journal):
    """Если run_triage падает — guard пишет в журнал, без сообщения оператору.

    Лог уводится в tmp: до 2026-07-26 этот тест писал FATAL в ПРОДОВЫЙ logs/triage.log, и лог
    ежедневно показывал свежие записи, пока настоящая доставка была мертва 13 дней."""
    monkeypatch.setattr(t, "LOG_FILE", tmp_path / "triage.log")

    def _boom(**k):
        raise RuntimeError("boom")
    monkeypatch.setattr(t, "run_triage", _boom)
    import notify
    sent = []
    monkeypatch.setattr(notify, "notify_operator", lambda msg, **k: sent.append(msg) or "telegram")
    res = t.run_triage_guarded(send_user_questions=True)
    assert res == {"auto_fixed": [], "needs_user": []}, "guard должен вернуть пустой результат"
    import json
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert sent == [] and len(records) == 1
    assert records[0]["where"] == "triage.run_triage"
    assert "RuntimeError: boom" in records[0]["text"]


def test_run_triage_guarded_passthrough(monkeypatch):
    """Норма: guard прозрачно отдаёт результат run_triage."""
    monkeypatch.setattr(t, "run_triage", lambda **k: {"auto_fixed": ["x"], "needs_user": []})
    assert t.run_triage_guarded() == {"auto_fixed": ["x"], "needs_user": []}


if __name__ == "__main__":
    test_non_whitelisted_warns_are_delivered()
    test_mute_list_is_dropped()
    test_special_phrasing_preserved()
    test_mute_list_short_and_justified()
    test_fdr_gate_sensor_warns_are_delivered()
    print("TEST PASS")


# ── Каденция по классу предупреждения (2026-08-31) ───────────────────────────
# Решение владельца: корзина «нужно твоё решение» — только для decide-класса;
# fix/standing уезжают понедельничным дайджестом. Умолчание — fix (с 30.09; было decide).

from datetime import date as _date

_MON = _date(2026, 8, 31)      # понедельник
_TUE = _date(2026, 9, 1)


def test_unknown_label_is_fix_and_not_lost():
    """Решение владельца 30.09: незнакомое — инженерам, не ему. Не теряется: уходит в
    отложенные (их читает ночной цикл через тот же integrity_latest.json)."""
    assert t.warn_class("нечто новое и странное") == "fix"
    daily, digest, deferred = t.split_by_cadence([["нечто новое", "x"]], _TUE)
    assert daily == [] and digest == [] and deferred == [["нечто новое", "x"]]


def test_fix_and_standing_go_to_digest_only_on_digest_weekday():
    ws = [["покрытие конверсии единиц (worklist канонизации)", "9: …"],
          ["survivorship: 3 constitution_conflicts старше 45д", "#682"],
          ["[health] промоут стоит: 1 строк ждут 60д", "CEA"]]
    daily, digest, deferred = t.split_by_cadence(ws, _TUE)
    assert daily == [ws[2]], daily                       # decide — всегда
    assert digest == [] and len(deferred) == 2           # вторник: отложено, не потеряно
    daily, digest, deferred = t.split_by_cadence(ws, _MON)
    assert daily == [ws[2]] and digest == deferred and len(digest) == 2


def test_probe_two_labels_two_cadences():
    """Свежее доказательство — дайджест; просроченное — ежедневно. Метки различаются текстом."""
    assert t.warn_class("security:pip_audit") == "fix"
    assert t.warn_class("Пробам судить не на чем (состояние данных)") == "standing"
    # 30.09: перепрогнать пробу — дело сессии, не владельца (умолчание fix).
    assert t.warn_class("Обещание проб на просроченном доказательстве (>90д)") == "fix"


def test_partner_thresholds_without_rows_are_standing():
    """Порог без входных строк остаётся standing по политике тенанта.
    Придуманный небольшой инвентарь не требует решения о клиническом состоянии."""
    assert t.warn_class("[health_partner] порогов без единой строки данных: 2 из 7") == "standing"


def test_uncovered_worktrees_notice_is_weekly_not_daily():
    """Решение владельца 21.09: «снимок не видит N деревьев» — standing, не decide.

    Замер: 17–21.09 строка приходила владельцу пять ночей подряд, и решать по ней было
    нечего — закоммиченная работа нитей копируется с 16.09, незакоммиченное в деревьях
    принято осознанно. Ежедневная строка без действия приучает пролистывать все строки
    (§13). Факт «про деревья не знаю» при этом не глушится — он едет в дайджест.
    И граница: соседняя строка того же датчика про НЕЗАКОММИЧЕННУЮ работу в главной
    копии этой правкой не трогалась. 23.09 её сдвинуло ОТДЕЛЬНОЕ решение владельца
    («да»: инженерное со стола): карточка 14.09 спрашивала его «сохранить файл в общую
    систему?» — вопрос, у которого оракул сессия, а не он; дерево и так снимается в
    refs/backups/wip каждые 3 ч. Теперь это fix: понедельничный дайджест и инженерная
    очередь. Утверждение оставлено, чтобы сдвиг был виден, а не случился молча.
    """
    assert t.warn_class("снимок MacBook не видит 2 дерев(о/а) нитей") == "standing"
    assert t.warn_class("на MacBook незакоммиченная работа: 1 файлов") == "fix"


def test_warn_classes_match_live_labels():
    """Ратчет против мёртвой подстроки (§18): каждая подстрока WARN_CLASSES обязана
    встречаться в исходнике датчиков, иначе класс назначен метке, которой нет.
    Двусторонне не судим — обратное («каждая метка классифицирована») и не требуется:
    умолчание decide законно."""
    import re
    src = "".join(open(t.SCRIPT_DIR / f, encoding="utf-8").read().lower()
                  for f in ("integrity_tests.py", "security_sensors.py", "producer_registry.py"))
    src = re.sub(r"\s+", " ", src)
    import finding_identity as fi
    dead = [sub for sub in fi.CLASS_BY_SUBSTRING if sub.lower() not in src]
    assert not dead, f"подстроки без живой метки: {dead}"


def test_warn_classes_values_are_known():
    import finding_identity as fi
    # До 30.09 умолчанием был decide, и в карту его не писали. Теперь умолчание fix, а
    # decide пишется ЯВНО, поимённо — только вопросы, чей оракул владелец.
    assert set(fi.CLASS_BY_SUBSTRING.values()) <= set(fi.CLASSES)
    assert fi.DEFAULT_CLASS == "fix", "незнакомое снова идёт на стол владельца"


# ── Вопросы человеку — в дом вопросов (28.09, нить triage-questions) ─────────────

def test_person_questions_skip_the_other_tenant():
    """Артефакт ночной проверки один на двоих: у владельца он несёт и находку партнёра
    с тегом [health_partner]. Человеку этой базы задаётся только СВОЙ вопрос."""
    qs = t.person_questions([
        ["[health] анализы требуют обновления: 200д", "последний: 2030-01-15"],
        ["[health_partner] анализы устарели: 512д (порог 270д)", ""],
    ], own_tag="health")
    assert [q["fingerprint"] for q in qs] == ["question:triage:labs:2030-01-15"]
    assert "2030-01-15" in qs[0]["content"] and "Когда планируешь" in qs[0]["content"]


def test_person_questions_period_and_untagged():
    qs = t.person_questions([
        ["анализы требуют обновления", ""],
        ["1 клинических периодов watchful_waiting без next phase",
         "требуют обновления после консультации с врачом: Выдуманный период"],
        ["invented worker failure", "FAKE_529"],
    ], own_tag="health")
    assert [q["kind"] for q in qs] == ["labs", "period"]
    assert "Выдуманный период" in qs[1]["content"]
    assert all("FAKE_529" not in q["content"] for q in qs)


def test_ask_reask_follows_memory_axis_and_not_over_an_open_lab_task(db, monkeypatch):
    """Открытый вопрос эпизода не дублируется; отвеченный перезадаётся ТОЛЬКО по оси
    времени памяти (should_ask_again) — своей оси срока канал не заводит
    (patient_answer_channel.reask_uses_memory_axis). И вопрос не задаётся поверх
    открытой задачи «сдать анализ» — иначе одно дело приходит человеку тремя
    сообщениями (холодное чтение 28.09)."""
    import health_db
    import task_agent
    q = t.person_questions([["анализы требуют обновления: 200д", "последний: 2030-01-15"]], "health")
    assert t._ask(q) == 1
    assert t._ask(q) == 0, "открытый — не дублируется"
    with health_db.get_conn() as conn:
        conn.execute("UPDATE tasks SET status='completed', resolved_text='Выдуманный ответ' "
                     "WHERE fingerprint='question:triage:labs:2030-01-15'")
    verdict = {"ask": False}
    monkeypatch.setattr(task_agent, "should_ask_again",
                        lambda fp: (verdict["ask"], "выдуманный вердикт оси"))
    assert t._ask(q) == 0, "ответ свежий — ось говорит «не перезадавать»"
    verdict["ask"] = True
    assert t._ask(q) == 1, "ответ протух по оси — вопрос возвращается"
    with health_db.get_conn() as conn:
        conn.execute("UPDATE tasks SET status='completed', resolved_text='Выдуманный ответ' "
                     "WHERE fingerprint='question:triage:labs:2030-01-15' AND status='open'")
    health_db.save_task(source="gp", type_="lab_test", content="Сдать выдуманный анализ")
    q2 = t.person_questions([["анализы требуют обновления: 250д", "последний: 2030-02-20"]], "health")
    assert t._ask(q2) == 0, "поверх открытой задачи сдать анализ — не спрашиваем"
