"""Характеризация weekly_digest (нить weekly-digest, 2026-09-05).

Что стережём: (1) отбор сюжетов — код, не модель; (2) гейт краснеет на подсадке и
молчит на чистом тексте (позит+негат); (3) решение outbox-читателя — таблица;
(4) секретные пути не доходят до промпта. API не зовётся: render/judge — вне периметра
(их оракул — dry-run на Studio, квитанция коммита).
"""
from datetime import datetime

import pytest

import weekly_digest as wd

C = wd.Commit


def _cs(n, subj="safety_net: x", files=("safety_net.py",)):
    return [C(f"{i:07x}", "2026-09-01", subj, "", list(files)) for i in range(n)]


def test_group_threads_by_handoff_marker_and_path():
    cs = [C("a", "2026-09-01", "docs(handoff): norm-from-documents — снимок", "", ["docs/handoff/norm-from-documents/x.md"]),
          C("b", "2026-09-01", "реестр: запись", "", ["docs/handoff/norm-from-documents/y.md"]),
          C("c", "2026-09-01", "safety_net: пол по величине", "", ["safety_net.py"]),
          C("d", "2026-09-01", "x", "", ["tests/t.py"])]
    g = wd._group_threads(cs)
    assert set(g) == {"norm-from-documents", "safety_net", "x"}
    assert len(g["norm-from-documents"]) == 2


def test_major_threads_is_code_not_model():
    threads = {"big": _cs(6), "mid": _cs(3), "small": _cs(2), "прочее": _cs(40)}
    assert wd._major_threads(threads) == ["big", "mid"]          # «прочее» и <3 — доделки
    raw = wd._threads_as_text(threads)
    assert raw.startswith("ОБЯЗАТЕЛЬНЫЕ СЮЖЕТЫ (в этом порядке): big, mid")
    assert "· small · 2 коммитов" not in raw or "доделка" in raw


def test_visible_by_hints():
    assert wd._is_visible(_cs(1, files=("jobs/scheduled.py",)))
    assert not wd._is_visible(_cs(1, files=("tests/unit/t.py",)))


@pytest.mark.parametrize("text,expect", [
    ("Пациент в ремиссии после лечения", "blocked"),        # литерал diagnosis_guard
    ("Показатель 12.5 ng/mL", "blocked"),                    # значение с единицей
    ("Измерено 29.08.2026", "blocked"),                      # дата измерения
    ("Сделали коммит и триаж", "blocked"),                   # жаргон из файла данных
    ("Тренд онкомаркеров теперь ждёт второго забора", "pass"),  # возможность — можно
    ("Каталог логики тестостерона", "pass"),                 # «лог»/«тест» внутри слов — нет
])
def test_gate_positive_and_negative(text, expect):
    lex = frozenset({"в ремиссии", "доктор_икс"})
    assert wd.gate_text(text, lex).verdict == expect, wd.gate_text(text, lex)


def test_header_line_not_judged():
    v = wd.gate_text("*Что изменилось за неделю: 31.08–06.09.2026*\n\n*А*\nчисто", frozenset(), raw="")
    assert v.verdict == "pass"


def test_unsupported_numbers_catches_invented_and_allows_sourced():
    assert wd._unsupported_numbers("три клика и 300 МБ, шкала 0 до 100", "score_0_100") == ["300"]
    assert wd.gate_text("39 дней молчал", frozenset(), raw="молчал 39 дней").verdict == "pass"
    assert "unsupported_numbers:40" in wd.gate_text("40 дней", frozenset(), raw="39 дней").hits_class[0]


def test_negative_control_mutation_kills_lexicon_hit():
    """Снятый лексикон → подсадка проходит: доказывает, что позитив выше держится
    лексиконом, а не случайным классом."""
    assert wd.gate_text("Пациент в ремиссии", frozenset()).verdict == "pass"


def test_sujets_split():
    t = "*Шапка*\n\n*А*\nтекст\n\n*Б*\nтекст\n\n*Ещё починили:* x"
    assert wd._sujets(t) == ["*А*\nтекст", "*Б*\nтекст"]


D = {"week": "2026-W36", "text": "t", "gate": {"verdict": "pass"}}
OK = {"health": "pass", "health_partner": "pass"}
SUN9 = datetime(2026, 9, 6, 9, 0)


@pytest.mark.parametrize("now,digest,verd,last,expect", [
    (SUN9, None, OK, None, "wait"),
    (datetime(2026, 9, 5, 23, 0), D, OK, None, "not_yet"),
    (datetime(2026, 9, 6, 8, 59), D, OK, None, "not_yet"),
    (SUN9, D, OK, None, "send"),
    (SUN9, D, {"health": "pass", "health_partner": None}, None, "wait"),
    (SUN9, D, {"health": "pass", "health_partner": "blocked"}, None, "blocked"),
    (SUN9, {**D, "text": None, "gate": {"verdict": "blocked"}}, OK, None, "blocked"),
    (SUN9, D, OK, "2026-W36", "done"),
    (SUN9, D, OK, "2026-W35", "send"),
])
def test_due_table(now, digest, verd, last, expect):
    assert wd.due(now, digest, verd, last) == expect


def test_scrub_secret_paths():
    assert wd._scrub_secret_paths("файл ~/.health_secrets/anthropic_key") == "файл <secret-path>"
    assert wd._scrub_secret_paths("/Users/x/.health_secrets_partner/telegram_token и") == "<secret-path> и"


def test_current_week_iso():
    assert wd.current_week(datetime(2026, 9, 5)) == "2026-W36"
    assert wd.current_week(datetime(2026, 9, 6)) == "2026-W36"
    assert wd.current_week(datetime(2026, 9, 7)) == "2026-W37"


# ── Разбор git-истории: коммит = коммит, а не обрезок имени файла (14.09) ──────

def _repo_with_commits(tmp_path, monkeypatch):
    """Настоящий маленький репозиторий. Подменяем ФАЙЛЫ (историю), а не слой под ними:
    предмет проверки — разбор вывода git, и мокать git значило бы проверять свой мок."""
    import subprocess
    r = tmp_path / "r"
    r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    for i, (subj, body, files) in enumerate([
        ("first: одно", "", ["a.py"]),
        # тело со слэшами — ровно то, на чём ломалась эвристика «строка со слэшем = файл»
        ("second: два файла", "ponytail:\n  reuse: ref:tests/conftest.py:git_bearing_src — есть",
         ["b.py", "docs/handoff/some-thread/x.md"]),
        ("docs(handoff): third-thread — снимок", "", ["c.py"]),
    ]):
        for f in files:
            p = r / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"x{i}", encoding="utf-8")
        run("git", "add", "-A")
        run("git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", subj, "-m", body)
    monkeypatch.setattr(wd, "ROOT", r, raising=True)
    return r


@pytest.mark.host_only
def test_collect_returns_one_record_per_commit(tmp_path, monkeypatch):
    """Позитив: сколько коммитов насчитал git, столько записей и вернул разборщик.

    Мутация исполнена 14.09: с прежним форматом (`%x1e` в КОНЕЦ, файлы по эвристике)
    живая история за четыре дня дала 97 «коммитов» вместо 96, у большинства sha равнялась
    обрезку имени файла ('BACKLOG', 'CLAUDE.'), а тело коммита попадало в files."""
    from datetime import date
    _repo_with_commits(tmp_path, monkeypatch)
    cs = wd._collect(date(2000, 1, 1), date.today())
    assert len(cs) == 3, [c.sha for c in cs]
    assert [c.subject for c in cs] == ["docs(handoff): third-thread — снимок",
                                       "second: два файла", "first: одно"]
    assert all(len(c.sha) == 7 and c.day.count("-") == 2 for c in cs), [(c.sha, c.day) for c in cs]


@pytest.mark.host_only
def test_body_lines_never_become_file_names(tmp_path, monkeypatch):
    """Негатив к тому же дефекту: строка тела со слэшем — не файл, и наоборот."""
    from datetime import date
    _repo_with_commits(tmp_path, monkeypatch)
    second = next(c for c in wd._collect(date(2000, 1, 1), date.today())
                  if c.subject.startswith("second"))
    assert sorted(second.files) == ["b.py", "docs/handoff/some-thread/x.md"]
    assert "ref:tests/conftest.py" in second.body
    assert not any("ref:" in f for f in second.files)


@pytest.mark.host_only
def test_thread_last_activity_takes_the_newest_commit(tmp_path, monkeypatch):
    from datetime import date
    _repo_with_commits(tmp_path, monkeypatch)
    last = wd.thread_last_activity(date.today(), lookback_days=9000)
    assert "third-thread" in last
    assert "some-thread" in last          # нить узнана по пути docs/handoff/<slug>/
    assert all(isinstance(v, date) for v in last.values())


@pytest.mark.host_only
def test_far_future_bound_would_silence_git(tmp_path, monkeypatch):
    """Грабля, найденная при написании тестов выше: `git log --until=2100-01-02` отдаёт
    ПУСТО с кодом 0 — разборщик тут ни при чём, столько ему и дали. Записано, чтобы
    следующий не искал дефект в коде: верхняя граница окна обязана быть реальной датой."""
    from datetime import date, timedelta
    _repo_with_commits(tmp_path, monkeypatch)
    assert wd._collect(date(2000, 1, 1), date(2099, 12, 31)) == []
    assert len(wd._collect(date(2000, 1, 1), date.today() + timedelta(days=1))) == 3
