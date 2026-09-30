"""Датчик незакоммиченной работы на MacBook — судится по снимку, с Studio.

Класс, который закрывается (2026-09-07): `uncommitted_watchdog` строился 17.05 под
«работу написали и не закоммитили», но с 23.05 код пишет MacBook, а сторож остался
на Studio и сторожит грязный деплой-таргет. Исходный класс не был покрыт ничем,
кроме суточного снимка `backup.sh`.

Замер, на котором стоит фильтр (14 хранящихся снимков, 2026-09-07): БЕЗ фильтра
датчик сигналил бы 14 дней из 14 — чистый шум; С фильтром 2 из 14, оба попадания
настоящие. Оба этих дня воспроизведены ниже дословно, вместе с одним тихим.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _stub(monkeypatch, *, refs, changed, cap, сообщение=""):
    """Подменяет ЕДИНСТВЕННЫЙ шов наружу — git_facts._git. Настоящий git в тест не
    ходит (§20: зелёный, причинённый состоянием диска машины, не зелёный)."""
    import integrity_tests as I
    import git_facts as gf
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))

    def fake(args):
        if args[0] == "for-each-ref":
            return refs
        if args[0] == "diff":
            return changed
        if args[0] == "log":
            # Сообщение коммита снимка несёт worktrees_uncovered=N — единственный
            # способ для датчика на Studio узнать про деревья нитей на MacBook
            # (внешнее ревью 14.09, F5).
            return сообщение
        raise AssertionError(f"датчик позвал git неожиданно: {args}")

    monkeypatch.setattr(gf, "_git", fake)
    return I


def _ref_line(days_ago: int, name="refs/backups/wip"):
    import integrity_tests as I
    return f"{name} {I.today - timedelta(days=days_ago)}"


# ── Причинные: три реальных снимка из тех самых четырнадцати ────────────────

def test_snapshot_2026_09_07_is_silent(monkeypatch):
    """⭐ Реальный снимок 07.09: единственный изменённый файл — CHANGELOG.md,
    дериватив doc_agent. Работы нет, датчик обязан молчать. Без фильтра здесь был
    бы сигнал — и так же в 11 днях из 14."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), changed="CHANGELOG.md\n", cap=cap)
    r = I.check_macbook_uncommitted()
    assert cap == [], f"шум вернулся: {cap}"
    assert r["uncommitted"] == [] and r["filtered_out"] == ["CHANGELOG.md"]


def test_snapshot_2026_07_30_is_loud(monkeypatch):
    """⭐ Реальный снимок 30.07: пять файлов настоящей работы — план пробы, его
    сайдкар-контракт, две доки и реестр замысла. Ровно то, ради чего датчик есть."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), cap=cap, changed=(
        "contracts/plans/probe_quarantine_exit_2026-07-29.py.json\n"
        "docs/explanation/validation_gate.md\n"
        "docs/how-to/adjudicate_quarantine.md\n"
        "plans/probe_quarantine_exit_2026-07-29.py\n"
        "subsystem_intent.yaml\n"))
    r = I.check_macbook_uncommitted()
    assert len(r["uncommitted"]) == 5
    assert any("незакоммиченная работа" in n for n, _ in cap), cap


def test_snapshot_2026_08_04_names_only_the_work(monkeypatch):
    """⭐ Реальный снимок 04.08: два деривативa и правка CLAUDE.md руками. Датчик
    обязан назвать ОДИН файл, а не три — иначе сигнал тонет в собственном шуме."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(1), cap=cap,
              changed="ARCH_SNAPSHOT.md\nCHANGELOG.md\nCLAUDE.md\n")
    r = I.check_macbook_uncommitted()
    assert r["uncommitted"] == ["CLAUDE.md"]
    name, detail = cap[0]
    assert "1 файлов" in name and "CLAUDE.md" in detail
    assert "CHANGELOG" not in detail, "дериватив просочился в текст владельцу"


# ── Возраст снимка: часть суждения, а не фон ────────────────────────────────

def test_stale_snapshot_names_both_readings(monkeypatch):
    """Снимок старше порога. Отсюда «ноут выключен» и «джоба мертва» неразличимы —
    и текст обязан назвать ОБА чтения, а не выбрать удобное. И до diff'а дело не
    доходит: судить о работе по протухшему снимку значило бы врать датой (§18)."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(4), changed=None, cap=cap)
    r = I.check_macbook_uncommitted()
    assert r["verdict"] == "снимок устарел"
    detail = cap[0][1]
    assert "выключен" in detail and "мертва" in detail


def test_fresh_boundary_is_still_judged(monkeypatch):
    """Ровно на пороге (3д) снимок ещё судится — иначе граница молчала бы в обе
    стороны и день терялся."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(3), changed="CLAUDE.md\n", cap=cap)
    r = I.check_macbook_uncommitted()
    assert r["uncommitted"] == ["CLAUDE.md"] and r["age_days"] == 3


def test_future_stamp_is_not_freshness(monkeypatch):
    """Штамп из будущего — сломанные часы, а не свежайший снимок (тот же age<0,
    что чинили в check_mc_gap и в рельсе доставки)."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(-2), changed=None, cap=cap)
    I.check_macbook_uncommitted()
    assert any("устарел" in n for n, _ in cap), cap


# ── Отказ инструмента ≠ «всё хорошо» ────────────────────────────────────────

def test_no_refs_at_all_is_loud(monkeypatch):
    """Снимков нет ни одного: незакоммиченная работа не защищена И не видна.
    Молчание здесь было бы худшим из возможных ответов."""
    cap = []
    I = _stub(monkeypatch, refs="", changed=None, cap=cap)
    assert I.check_macbook_uncommitted() is None
    assert any("нет ни одного" in n for n, _ in cap), cap


def test_git_unavailable_is_loud(monkeypatch):
    """git не ответил (песочница без .git, сломанный репозиторий) — датчик ослеп,
    и он обязан это СКАЗАТЬ. Тихий возврат был бы тем самым классом «исключение →
    тишина», за которым в проекте ходит AST-сторож."""
    cap = []
    I = _stub(monkeypatch, refs=None, changed=None, cap=cap)
    assert I.check_macbook_uncommitted() is None
    assert any("не читаются" in n for n, _ in cap), cap


def test_diff_failure_is_loud(monkeypatch):
    """Ref есть, а diff не отработал — снова «ослеп», а не «работы нет»."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), changed=None, cap=cap)
    assert I.check_macbook_uncommitted() is None
    assert any("не сравнивается" in n for n, _ in cap), cap


def test_filter_comes_from_single_home(monkeypatch):
    """Датчик читает фильтр из git_facts, а не из своей копии. Мутация «завести
    список имён внутри integrity_tests» обязана уронить этот тест."""
    import git_facts as gf
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), changed="CHANGELOG.md\n", cap=cap)
    monkeypatch.setattr(gf, "MACHINE_REGENERATED_FILES", ())   # дом опустел
    r = I.check_macbook_uncommitted()
    assert r["uncommitted"] == ["CHANGELOG.md"], "фильтр взят не из git_facts"


def test_wip_ref_wins_over_older_daily(monkeypatch):
    """Свежайший снимок выбирается по дате, а не по имени: перекатывающийся wip
    (каждые 3ч) обязан побеждать вчерашний суточный."""
    cap = []
    refs = (f"{_ref_line(0)}\n"
            f"refs/backups/daily-2000-01-01 2000-01-01\n")
    I = _stub(monkeypatch, refs=refs, changed="CLAUDE.md\n", cap=cap)
    r = I.check_macbook_uncommitted()
    assert r["age_days"] == 0 and r["uncommitted"] == ["CLAUDE.md"]


@pytest.mark.host_only
def test_не_ascii_путь_доезжает_до_читателя_не_экранированным():
    """Датчик обязан называть файл так, как человек его видит.

    Внешнее ревью 14.09: `git diff --name-only` без core.quotepath=false отдаёт
    «"\\320\\275\\320\\276..."». Читатель вычитает MACHINE_REGENERATED_FILES
    СРАВНЕНИЕМ СТРОК — значит любой не-ASCII артефакт из фильтра перестал бы
    фильтроваться молча; а человеку датчик назвал бы имя, которого он у себя
    не найдёт. Проверяется НАСТОЯЩИЙ git в одноразовом репозитории, не заглушка.
    """
    import os as _os, subprocess as _sp, tempfile as _tf, sys as _sys
    from pathlib import Path as _P
    with _tf.TemporaryDirectory() as t:
        repo = _P(t) / "repo"; repo.mkdir()
        e = dict(_os.environ); e["HOME"] = t
        for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_AUTHOR_NAME",
                  "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            e.pop(v, None)
        def g(*a):
            return _sp.run(["git", "-C", str(repo), *a], capture_output=True,
                           text=True, timeout=60, env=e)
        g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        (repo / "f.txt").write_text("base\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "base", "--no-verify")
        (repo / "новый_модуль.py").write_text("x = 1\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "кириллица", "--no-verify")

        import importlib, git_facts as gf
        старый = gf.ROOT
        try:
            gf.ROOT = repo
            имена = (gf._git(["diff", "--name-only", "HEAD^", "HEAD"]) or "").split()
        finally:
            gf.ROOT = старый
    assert "новый_модуль.py" in имена, (
        f"читатель отдал экранированное имя вместо настоящего: {имена}")


def test_снимок_не_видит_деревья_нитей_и_говорит_об_этом(monkeypatch):
    """«Не знаю» вместо «всё хорошо» (внешнее ревью 14.09, F5).

    Снимок берётся с ГЛАВНОЙ копии, работа нити живёт в своём дереве под
    $HOME/.worktrees. Раньше датчик при чистой главной копии молчал — и его
    молчание читалось как «незакоммиченного нет», хотя честный ответ «про эти
    деревья я не знаю». Чем строже соблюдают §21, тем больше работы уходит из-под
    датчика, и тем увереннее он молчит.
    """
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), changed="", cap=cap,
              сообщение="snapshot: wip backup 2026-09-14 (вне main) worktrees_uncovered=2")
    r = I.check_macbook_uncommitted()
    assert r["worktrees_uncovered"] == 2, r
    assert any("не видит 2" in n for n, _ in cap), (
        f"датчик промолчал про непокрытые деревья — это «всё хорошо» вместо «не знаю»: {cap}")


def test_без_деревьев_нитей_датчик_молчит_как_прежде(monkeypatch):
    """Обратная сторона: без деревьев нитей лишней строки быть не должно."""
    cap = []
    I = _stub(monkeypatch, refs=_ref_line(0), changed="", cap=cap,
              сообщение="snapshot: wip backup 2026-09-14 (вне main) worktrees_uncovered=0")
    r = I.check_macbook_uncommitted()
    assert r["worktrees_uncovered"] == 0, r
    assert cap == [], f"датчик заговорил без повода: {cap}"
