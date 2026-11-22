"""Датчик ошибок в stderr-логах джоб: судит ТОЛЬКО дописанное с прошлой проверки.

Повод: 13.09 вотчер авто-тестов девять суток писал в свой stderr «Read-only file
system», следил за всей файловой системой вместо репозитория — и остался невидим, потому
что PID у него был. Замер по 38 джобам нашёл ошибки в семи логах, ни одного читателя.

Почему смещение, а не хвост: у планировщика опросников трассировки трёхмесячной давности
лежат в пределах последних 400 строк и попали бы в сегодняшнюю находку. Проверка на это —
test_old_errors_are_not_a_finding.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import stderr_watch as sw


def _agent(dir_: Path, label: str, log: Path):
    (dir_ / f"{label}.plist").write_bytes(plistlib.dumps(
        {"Label": label, "StandardErrorPath": str(log)}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    agents = tmp_path / "LaunchAgents"; agents.mkdir()
    logs = tmp_path / "logs"; logs.mkdir()
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(agents))
    monkeypatch.setenv("HEALTH_STDERR_WATCH_STATE", str(tmp_path / "state.json"))
    return agents, logs


def test_old_errors_are_not_a_finding(env):
    """Первый прогон только запоминает позиции: датчик не рождается красным на истории."""
    agents, logs = env
    log = logs / "old.err.log"
    log.write_text("Traceback (most recent call last):\nдревняя беда\n", encoding="utf-8")
    _agent(agents, "com.larry.health.old", log)
    assert sw.new_errors() == [], "накопленная история выдана за новую находку"


def test_new_error_is_found(env):
    """Позитив: ошибка, дописанная ПОСЛЕ прошлой проверки, становится находкой."""
    agents, logs = env
    log = logs / "job.err.log"
    log.write_text("2026-09-13 INFO всё хорошо\n", encoding="utf-8")
    _agent(agents, "com.larry.health.job", log)
    sw.new_errors()                                    # запомнили позицию
    with log.open("a", encoding="utf-8") as f:
        f.write("mkdir: //logs: Read-only file system\n")
    found = sw.new_errors()
    assert len(found) == 1 and found[0]["label"] == "com.larry.health.job"
    assert "Read-only file system" in found[0]["sample"]


def test_info_lines_are_not_errors(env):
    """Негативный контроль: stderr законно используется под INFO (так пишут
    calendar_sync и lab_intake). Без него правило «любая новая строка = находка» тоже
    прошло бы позитив выше, и датчик красил бы половину джоб каждую ночь."""
    agents, logs = env
    log = logs / "chatty.err.log"
    log.write_text("start\n", encoding="utf-8")
    _agent(agents, "com.larry.health.chatty", log)
    sw.new_errors()
    with log.open("a", encoding="utf-8") as f:
        f.write("2026-09-13 INFO Calendar cache: 55 событий\n2026-09-13 INFO token refreshed\n")
    assert sw.new_errors() == [], "INFO-строки выданы за ошибки"


def test_rotation_does_not_fire(env):
    """Ротация (файл стал меньше) — не залп: позиция сбрасывается молча."""
    agents, logs = env
    log = logs / "rot.err.log"
    log.write_text("x" * 500 + "\n", encoding="utf-8")
    _agent(agents, "com.larry.health.rot", log)
    sw.new_errors()
    log.write_text("Traceback (most recent call last):\n", encoding="utf-8")  # усечён
    assert sw.new_errors() == [], "ротация дала ложную находку"
    # а вот следующая дописанная ошибка — уже находка
    with log.open("a", encoding="utf-8") as f:
        f.write("ModuleNotFoundError: no module named x\n")
    assert len(sw.new_errors()) == 1


def test_traceback_sample_is_exception_line_not_header(env):
    """Карточка 18.11: у бота «+4: Traceback (most recent call last):» — образец нёс
    заголовок трассировки, по которому причину не назвать. Образец обязан нести итог
    трассировки (последнее исключение цепочки). Значения выдуманы."""
    agents, logs = env
    log = logs / "bot.err.log"
    log.write_text("start\n", encoding="utf-8")
    _agent(agents, "com.larry.health.bot", log)
    sw.new_errors()
    with log.open("a", encoding="utf-8") as f:
        f.write("Traceback (most recent call last):\n"
                '  File "x.py", line 1, in <module>\n'
                "    raise KeyError('a')\n"
                "KeyError: 'a'\n\n"
                "During handling of the above exception, another exception occurred:\n\n"
                "Traceback (most recent call last):\n"
                '  File "x.py", line 3, in <module>\n'
                "telegram.error.NetworkError: выдуманная сеть\n")
    found = sw.new_errors()
    assert len(found) == 1 and found[0]["hits"] == 2
    assert found[0]["sample"] == "telegram.error.NetworkError: выдуманная сеть"


def test_truncated_traceback_falls_back_to_header(env):
    """Граница: трассировка, обрезанная концом дописанного куска, — образец остаётся
    заголовком, а не пустотой и не чужой строкой."""
    agents, logs = env
    log = logs / "cut.err.log"
    log.write_text("start\n", encoding="utf-8")
    _agent(agents, "com.larry.health.cut", log)
    sw.new_errors()
    with log.open("a", encoding="utf-8") as f:
        f.write('Traceback (most recent call last):\n  File "x.py", line 1\n')
    assert sw.new_errors()[0]["sample"] == "Traceback (most recent call last):"


def _sample_of(env, name, text):
    agents, logs = env
    log = logs / f"{name}.err.log"
    log.write_text("start\n", encoding="utf-8")
    _agent(agents, f"com.larry.health.{name}", log)
    sw.new_errors()
    with log.open("a", encoding="utf-8") as f:
        f.write(text)
    return sw.new_errors()[0]["sample"]


def test_foreign_line_after_unclosed_traceback_is_not_sample(env):
    """Ревью 18.11, п.1–2: после незакрытой трассировки идёт строка лога — она не итог."""
    assert _sample_of(env, "foreign", 'Traceback (most recent call last):\n  File "x.py", line 1\n'
                      "2031-01-01 INFO выдуманная строка лога\n") == "Traceback (most recent call last):"


def test_exception_group_sample_is_group_line(env):
    """Ревью 18.11, п.1: у группы исключений все строки с отступом — итог из «| ExceptionGroup: …»,
    а не следующая строка лога без отступа."""
    text = ("  + Exception Group Traceback (most recent call last):\n"
            '  |   File "x.py", line 1, in <module>\n'
            "  | ExceptionGroup: выдуманная группа (1 sub-exception)\n"
            "  +-+---------------- 1 ----------------\n"
            "    | ValueError: выдумка\n"
            "    +------------------------------------\n"
            "2031-01-01 INFO выдуманная строка лога\n")
    assert _sample_of(env, "group", text) == "ExceptionGroup: выдуманная группа (1 sub-exception)"


def test_chain_cut_after_during_handling_falls_back_to_header(env):
    """Ревью 18.11, п.3: обрыв после «During handling…» — не первое исключение, а заголовок."""
    text = ('Traceback (most recent call last):\n  File "x.py", line 1\nKeyError: \'a\'\n\n'
            "During handling of the above exception, another exception occurred:\n\n"
            'Traceback (most recent call last):\n  File "x.py", line 3\n')
    assert _sample_of(env, "chaincut", text) == "Traceback (most recent call last):"


def test_plist_without_stderr_path_is_skipped(env):
    agents, logs = env
    (agents / "com.larry.health.quiet.plist").write_bytes(
        plistlib.dumps({"Label": "com.larry.health.quiet"}))
    assert sw.new_errors() == []


# ── Слепота самого датчика (13.09, мина 1) ───────────────────────────────────
# «Ошибок нет» и «я ничего не смотрел» снаружи выглядят одинаково — тихо.
# Эти оракулы существуют, чтобы второе перестало быть тихим.

def test_missing_state_is_blindness(env):
    """Состояния нет — датчик ни разу не отработал, и его молчание ничего не значит."""
    agents, logs = env
    log = logs / "a.err.log"; log.write_text("x\n")
    _agent(agents, "com.larry.health.a", log)
    blind = sw.blind_spots()
    assert blind and "ни разу не отработал" in blind[0]


def test_stale_state_is_blindness(env, monkeypatch):
    """Состояние есть, но не обновлялось дольше порога — датчик не бежит."""
    import os
    import time
    agents, logs = env
    log = logs / "a.err.log"; log.write_text("x\n")
    _agent(agents, "com.larry.health.a", log)
    sw.new_errors()
    state = Path(os.environ["HEALTH_STDERR_WATCH_STATE"])
    old = time.time() - 40 * 3600
    os.utime(state, (old, old))
    blind = sw.blind_spots()
    assert any("не обновлялось 40 ч" in b for b in blind), blind


def test_tracking_fewer_logs_than_declared_is_blindness(env):
    """Плистов стало больше, состояние отстало — датчик смотрит не на всё."""
    agents, logs = env
    a = logs / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    sw.new_errors()
    b = logs / "b.err.log"; b.write_text("y\n")
    _agent(agents, "com.larry.health.b", b)
    blind = sw.blind_spots()
    assert any("объявляют 2" in x and "отслеживает 1" in x for x in blind), blind


def test_not_yet_created_log_is_tracked_not_blind(env):
    """Карточка 28.09 «объявляют 28, отслеживает 12»: лог, которого ещё нет на диске
    (джоба не писала в stderr), выпадал из состояния и считался слепотой. Его отсутствие —
    позиция 0, а не слепое пятно; и первая ошибка в появившемся файле — находка."""
    agents, logs = env
    a = logs / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    b = logs / "b.err.log"                              # файла нет
    _agent(agents, "com.larry.health.b", b)
    sw.new_errors()
    assert sw.blind_spots() == []
    sw.new_errors()
    b.write_text("Traceback (most recent call last):\n", encoding="utf-8")
    found = sw.new_errors()
    assert [f["label"] for f in found] == ["com.larry.health.b"], found


def test_fresh_full_state_is_silent(env):
    """Негативный контроль: здоровый датчик молчит — иначе находка обесценится."""
    agents, logs = env
    a = logs / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    sw.new_errors()
    assert sw.blind_spots() == []


def test_liveness_check_runs_before_error_check():
    """Порядок в integrity_tests: сначала слепота, потом сбор ошибок.

    new_errors() ПЕРЕПИСЫВАЕТ состояние. Стой проверка слепоты после него, она
    всегда видела бы свежую отметку — зелёный был бы причинён порядком строк, а
    не работой датчика (§20). Тест судит ИСХОДНИК, потому что судить нечего
    другого: порядок проверок задаётся порядком вызовов check() на импорте.
    """
    src = (Path(__file__).resolve().parents[2] / "integrity_tests.py").read_text(
        encoding="utf-8")
    i_blind = src.index('check("слепота stderr-датчика"')
    i_err = src.index('check("новые ошибки в stderr джоб"')
    assert i_blind < i_err, "проверка слепоты обязана быть зарегистрирована РАНЬШЕ сбора"


def test_unreadable_or_parentless_log_stays_blind(env):
    """Ревью починки 01.10, п.1: «файла ещё нет» — только FileNotFoundError при живом
    каталоге. Отказ доступа и отсутствие каталога — слепота, а не здоровье."""
    import os
    agents, logs = env
    locked = logs / "locked"; locked.mkdir()
    a = locked / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    _agent(agents, "com.larry.health.b", logs / "нет-каталога" / "b.err.log")
    os.chmod(locked, 0)
    try:
        sw.new_errors(); sw.new_errors()
        assert sw.blind_spots() != []
    finally:
        os.chmod(locked, 0o755)


def test_blindness_names_untracked_logs_and_why(env):
    """Карточка 28.09 после починки 01.10: «объявляют 28, отслеживает 18» — и ни одного
    имени. Чинить такое нечем: причина живёт в среде, а какие 10 логов — неизвестно.
    Слепота обязана назвать лейбл и причину (нет каталога / нет доступа)."""
    import os
    agents, logs = env
    a = logs / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    locked = logs / "locked"; locked.mkdir()
    (locked / "c.err.log").write_text("x\n")
    _agent(agents, "com.larry.health.c", locked / "c.err.log")
    _agent(agents, "com.larry.health.b", logs / "нет-каталога" / "b.err.log")
    os.chmod(locked, 0)
    try:
        sw.new_errors()
        msg = " ".join(sw.blind_spots())
    finally:
        os.chmod(locked, 0o755)
    assert "com.larry.health.b" in msg and "нет каталога" in msg, msg
    assert "com.larry.health.c" in msg and "нет доступа" in msg, msg
    assert "com.larry.health.a:" not in msg, msg       # отслеживаемый не назван слепым


def test_temporary_access_loss_does_not_replay_history(env):
    """Ревью п.2: учтённый Traceback → временный отказ доступа → доступ вернулся, файл
    тот же — старая ошибка не выдаётся повторно."""
    import os
    agents, logs = env
    d = logs / "d"; d.mkdir()
    a = d / "a.err.log"; a.write_text("x\n")
    _agent(agents, "com.larry.health.a", a)
    sw.new_errors()
    a.write_text("x\nTraceback (most recent call last):\n")
    assert sw.new_errors()                      # новая ошибка — находка
    os.chmod(d, 0)
    try:
        sw.new_errors()
    finally:
        os.chmod(d, 0o755)
    assert sw.new_errors() == []
