"""Алерт «pytest упал» различает НОВОЕ и ХРОНИЧЕСКОЕ (run_checks.sh::pytest_failed_diff).

Замер 2026-08-30: test_clone_is_skipped_and_says_so был красным каждый день с 2026-07-30
(31 алерт подряд), и новый красный того же утра в алерте не отличался от месячного.
Функция вырезается из run_checks.sh по маркерам и исполняется настоящим bash — оракул
бьёт по тому коду, который идёт в 07:50, а не по его пересказу на Python.

КОНТРАКТ СМЕНИЛСЯ 2026-09-02 (решение владельца). Замер: 42 красных утренних прогона
против 28 зелёных — печатать «ХРОНИЧЕСКОЕ» каждый день значило слать фон. Теперь пустая
строка = молчим, и говорим только об ИЗМЕНЕНИИ состояния: новый id; возраст ≥3 дней
(первое напоминание); ≥7 дней (второе и последнее). Состояние получило третью колонку —
сколько напоминаний уже отправлено.

ДОМ ОДИН. Тесты, заведённые 02.09 в test_pytest_alert_narrowing.py, слиты сюда: два
файла на одну функцию — второй дом оракула, он разошёлся бы с первым молча. Файл-дубль
удалён тем же коммитом.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _fn_source() -> str:
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    m = re.search(r"# --- pytest_diff begin ---\n(.*?)# --- pytest_diff end ---", src, re.S)
    assert m, "маркеры pytest_diff в run_checks.sh пропали — тест бьёт мимо"
    return m.group(1)


def _run(state: Path, ids: list[str], tmp_path: Path) -> tuple[str, str]:
    today = tmp_path / "today.txt"
    today.write_text("\n".join(ids) + "\n", encoding="utf-8")
    script = _fn_source() + f'\npytest_failed_diff "{state}" "{today}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout, state.read_text(encoding="utf-8") if state.exists() else ""


def test_first_failure_is_new_and_dated(tmp_path):
    """Замысел прежний (новое названо и датировано); формат состояния — три колонки."""
    state = tmp_path / "state.tsv"
    out, st = _run(state, ["tests/a.py::t1"], tmp_path)
    assert "НОВОЕ: tests/a.py::t1" in out
    assert re.search(r"^tests/a\.py::t1\t\d{4}-\d{2}-\d{2}\t0$", st, re.M)


def test_new_id_is_named_while_old_one_stays_silent(tmp_path):
    """Прежний test_repeat_is_chronic_…, приведённый к контракту 02.09.

    Замысел сохранён: новое отделено от старого и дата первого провала не теряется.
    Изменился ИСХОД для старого — раньше он печатался «ХРОНИЧЕСКОЕ» каждый день,
    теперь молчит: повтор уже сообщённого не несёт нового факта.
    """
    state = tmp_path / "state.tsv"
    state.write_text("tests/a.py::t1\t2026-07-30\t2\n", encoding="utf-8")
    out, st = _run(state, ["tests/a.py::t1", "tests/b.py::t2"], tmp_path)
    assert "НОВОЕ: tests/b.py::t2" in out
    assert "tests/a.py::t1" not in out, "уже сообщённое обязано молчать (решение 02.09)"
    assert "tests/a.py::t1\t2026-07-30" in st, "дата первого провала потеряна"
    assert "tests/b.py::t2\t" in st


def test_fixed_failure_leaves_state(tmp_path):
    """Починенный тест исчезает из состояния: вернувшись, он снова НОВОЕ, а не «с июля»."""
    state = tmp_path / "state.tsv"
    state.write_text("tests/a.py::t1\t2026-07-30\ntests/b.py::t2\t2026-08-02\n", encoding="utf-8")
    out, st = _run(state, ["tests/b.py::t2"], tmp_path)
    assert "tests/a.py::t1" not in st and "tests/a.py::t1" not in out


# ── контракт 02.09: молчание как легальный исход (слито из test_pytest_alert_narrowing) ──

def _run2(tmp_path, ids, state_lines=None):
    """Тонкая обёртка над _run: состояние задаётся строками, возвращает (вывод, состояние)."""
    state = tmp_path / "state2.tsv"
    if state_lines is not None:
        state.write_text("".join(state_lines), encoding="utf-8")
    return _run(state, ids, tmp_path)


def _aged(day_id, days):
    import datetime as dt
    return f"{day_id}\t{(dt.date.today() - dt.timedelta(days=days)).isoformat()}\t0\n"


def test_new_failure_speaks_once(tmp_path):
    msg, state = _run2(tmp_path, ["tests/unit/test_a.py::test_x"], state_lines=[])
    assert "🆕" in msg and "test_x" in msg
    assert "test_x" in state


def test_same_set_next_day_is_silence(tmp_path):
    """ГЛАВНЫЙ: тот же набор упавших назавтра не даёт НИ ОДНОГО сообщения."""
    ids = ["tests/unit/test_a.py::test_x"]
    _run2(tmp_path, ids, state_lines=[])          # первый день — сказали
    msg, _ = _run2(tmp_path, ids)                 # второй день — обязаны молчать
    assert msg == "", f"хроническое повторение обязано молчать, а пришло: {msg!r}"


def test_third_day_escalates_once(tmp_path):
    ids = ["tests/unit/test_a.py::test_x"]
    msg, state = _run2(tmp_path, ids, state_lines=[_aged(ids[0], 3)])
    assert "⏳" in msg and "3 дн" in msg
    assert state.strip().endswith("\t1"), "напоминание обязано пометиться как отправленное"
    msg2, _ = _run2(tmp_path, ids)
    assert msg2 == "", "напоминание одноразовое, иначе оно станет новым фоном"


def test_week_escalates_second_and_last_time(tmp_path):
    ids = ["tests/unit/test_a.py::test_x"]
    import datetime as dt
    d = (dt.date.today() - dt.timedelta(days=8)).isoformat()
    msg, state = _run2(tmp_path, ids, state_lines=[f"{ids[0]}\t{d}\t1\n"])
    assert "⏳" in msg and "8 дн" in msg
    assert state.strip().endswith("\t2")
    msg2, _ = _run2(tmp_path, ids)
    assert msg2 == "", "после второго напоминания молчим навсегда по этому id"


def test_missed_run_does_not_eat_the_reminder(tmp_path):
    """Возраст, а не «ровно третий день»: прогон в тот день мог не случиться."""
    ids = ["tests/unit/test_a.py::test_x"]
    msg, _ = _run2(tmp_path, ids, state_lines=[_aged(ids[0], 5)])
    assert "⏳" in msg, "поломка старше порога обязана напомнить, даже если день пропущен"


def test_new_id_inside_old_red_is_not_masked(tmp_path):
    """Новая регрессия внутри уже красного набора обязана быть видна."""
    old = "tests/unit/test_a.py::test_x"
    msg, _ = _run2(tmp_path, [old, "tests/unit/test_b.py::test_y"],
                  state_lines=[_aged(old, 1)])
    assert "🆕" in msg and "test_y" in msg and "test_x" not in msg


def test_recovery_state_lives_in_tree_not_shared_tmp(tmp_path):
    """R1: общий /tmp давал песочнице писать состояние боевого дерева."""
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    assert "/tmp/health_pytest_last_fail" not in src, \
        "состояние recovery обязано жить в своём дереве — общий /tmp делят два чекаута"
    assert '_PYTEST_FAIL_STATE="$SCRIPT_DIR/logs/' in src


def test_alert_receipt_gates_recovery_ping(tmp_path):
    """«Снова зелёный» закрывает только ту петлю, которую владельцу открыли."""
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    red = src[src.index("PYTEST_OK=false"):src.index("rm -f \"$_PYTEST_OUT\"")]
    assert red.count("_PYTEST_FAIL_STATE\"") == 1, \
        "состояние recovery пишется ровно в одном месте — там, где реально отправили"
    assert 'if [ -n "$_FAILED_DIFF" ]; then' in red


def test_watcher_secrets_go_through_the_home(tmp_path):
    """Прямой curl с $HOME/.health_secrets не изолируется HEALTH_SECRETS_DIR."""
    w = (ROOT / "watch_and_test.sh").read_text(encoding="utf-8")
    assert "HOME/.health_secrets/telegram" not in w, \
        "отправка обязана идти через notify.py (дом секретов), а не мимо него"
    assert "notify.fault" in w and "person_key=None" in w


def test_watcher_tree_is_computed_not_hardcoded(tmp_path):
    """Копия в песочнице не должна гонять тесты и алерты о БОЕВОМ дереве.

    Здесь остаётся ТОЛЬКО то, что проверяется текстом: путь не захардкожен и марки
    ключуются деревом. Само вычисление дерева текстом проверять нельзя — эта проверка
    ровно так и провалилась: с 02.09 она требовала дословной строки с ${BASH_SOURCE[0]},
    была зелёной девять суток подряд, а механизм под настоящей zsh-шапкой давал "/".
    Написание — не поведение (тот же класс, что грепающий контроль границы dispgate).
    Поведение стережёт tests/unit/test_watch_and_test_tree.py — запуск /bin/zsh из
    cwd="/" на копии в tmp, мутация исполнена. Второго дома этому утверждению не заводить.
    """
    w = (ROOT / "watch_and_test.sh").read_text(encoding="utf-8")
    assert 'SCRIPT_DIR="/Users/' not in w and 'SCRIPT_DIR="$HOME' not in w
    assert "_TREE_KEY" in w and "health_alert_sent_${key}.${_TREE_KEY}" in w


def test_deadman_ping_uses_secrets_home(tmp_path):
    """Песочница не должна пинговать боевой dead-man (ложная живость, §14)."""
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    assert 'HOME/.health_secrets/healthcheck_url' not in src
    assert "secrets_dir()" in src


def _ids(out_text: str, tmp_path: Path) -> list[str]:
    out = tmp_path / "pytest.out"
    out.write_text(out_text, encoding="utf-8")
    script = _fn_source() + f'\npytest_failed_ids "{out}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_red_run_without_failed_lines_is_named(tmp_path):
    """Замер 30.09 07:50, первое утро в контейнере: INTERNALERROR на сборе, ни одной строки
    FAILED — пустой набор «не изменился» против пустого, и мёртвый прогон промолчал."""
    assert _ids("INTERNALERROR> FileNotFoundError: git\n", tmp_path) == ["pytest-не-запустился"]
    assert _ids("FAILED tests/a.py::t1 - boom\nFAILED tests/a.py::t1 - x\n", tmp_path) == ["tests/a.py::t1"]


def test_crash_id_is_new_then_silent(tmp_path):
    """Имя падения проходит ту же хронику: первое утро — НОВОЕ, повтор молчит."""
    state = tmp_path / "state.tsv"
    out, _ = _run(state, ["pytest-не-запустился"], tmp_path)
    assert "НОВОЕ: pytest-не-запустился" in out
    out, _ = _run(state, ["pytest-не-запустился"], tmp_path)
    assert out == ""


def test_error_lines_are_named_too(tmp_path):
    """05.10.2026: пять ERROR каждую ночь шли без имени — набор строился только из FAILED."""
    out = "FAILED tests/a.py::t1 - boom\nERROR tests/b.py::t2 - fixture\n"
    assert _ids(out, tmp_path) == ["tests/a.py::t1", "tests/b.py::t2"]
    assert _ids("ERROR tests/b.py::t2 - x\n", tmp_path) == ["tests/b.py::t2"]


def test_nightly_summary_lists_errors():
    """Без E в -r сводка pytest ERROR не перечисляет — функции нечего прочитать."""
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    m = re.search(r"\$PY -m pytest tests/ [^>]*-r(\w+)", src)
    assert m and "E" in m.group(1) and "f" in m.group(1), m and m.group(0)


def test_tenant_start_line_goes_to_tenant_log():
    """05.10.2026: строка старта проверки партнёра писалась в журнал владельца до смены LOG."""
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    assert '[[ "$SCHEDULED" == "true" ]] || _start_line' in src
    i_switch = src.index('LOG="$TLOGS/run_checks.log"')
    assert src.index("_start_line", i_switch) - i_switch < 80
