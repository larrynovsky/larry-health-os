"""test_log_rotate — ротация логов без root (2026-09-01).

Главный оракул: COPYTRUNCATE, а не rename. launchd и logging.FileHandler держат открытый
дескриптор; переименование файла оставит писателя в переименованном inode, и живой лог
замолчит навсегда. Тест ловит это по inode — на реализации через rename он красный.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import log_rotate

pytestmark = pytest.mark.unit


def _conf(tmp: Path, target: Path, keep: int = 2, size_kb: int = 1) -> Path:
    c = tmp / "c.conf"
    c.write_text(f"# комментарий\n\n{target} user:staff 644 {keep} {size_kb} * J\n", encoding="utf-8")
    return c


def test_copytruncate_keeps_inode_and_empties_file(tmp_path):
    p = tmp_path / "big.log"
    p.write_bytes(b"a" * 5000)
    ino = p.stat().st_ino
    done = log_rotate.rotate_all(_conf(tmp_path, p))
    assert len(done) == 1 and done[0]["inode_kept"] is True
    assert p.exists() and p.stat().st_size == 0, "файл обязан остаться и опустеть"
    assert p.stat().st_ino == ino, "inode изменился — писатель осиротел бы"
    assert (tmp_path / "big.log.0.gz").exists()


def test_open_writer_keeps_writing_after_rotation(tmp_path):
    """Живой писатель (как launchd) продолжает писать В ТОТ ЖЕ файл после ротации."""
    p = tmp_path / "live.log"
    p.write_bytes(b"b" * 4000)
    with open(p, "a") as writer:                    # держим дескриптор, как launchd
        log_rotate.rotate_all(_conf(tmp_path, p))
        writer.write("после ротации\n")
        writer.flush()
    assert "после ротации" in p.read_text(), "запись ушла мимо файла — ротация осиротила писателя"


def test_below_threshold_not_rotated(tmp_path):
    p = tmp_path / "small.log"
    p.write_bytes(b"c" * 100)
    assert log_rotate.rotate_all(_conf(tmp_path, p, size_kb=1)) == []
    assert not (tmp_path / "small.log.0.gz").exists()


def test_archives_shift_and_oldest_dropped(tmp_path):
    p = tmp_path / "r.log"
    conf = _conf(tmp_path, p, keep=2)
    for mark in (b"1", b"2", b"3"):
        p.write_bytes(mark * 3000)
        log_rotate.rotate_all(conf)
    import gzip
    assert gzip.open(tmp_path / "r.log.0.gz", "rb").read()[:1] == b"3"   # свежий
    assert gzip.open(tmp_path / "r.log.1.gz", "rb").read()[:1] == b"2"
    assert not (tmp_path / "r.log.2.gz").exists(), "keep=2 — третий архив обязан исчезнуть"


def test_missing_file_and_broken_line_are_skipped(tmp_path):
    """Конфиг общий для двух машин: чужой путь пропускается молча, битая строка — с warning."""
    c = tmp_path / "c.conf"
    c.write_text(f"{tmp_path}/нет.log user:staff 644 2 1 * J\nмусор мусор\n", encoding="utf-8")
    assert log_rotate.rotate_all(c) == []


def test_dry_run_changes_nothing(tmp_path):
    p = tmp_path / "d.log"
    p.write_bytes(b"d" * 3000)
    done = log_rotate.rotate_all(_conf(tmp_path, p), dry_run=True)
    assert len(done) == 1 and done[0]["dry_run"] is True
    assert p.stat().st_size == 3000 and not (tmp_path / "d.log.0.gz").exists()


# ── датчик слепого пятна: лог растёт, а в конфиге его нет ─────────────────────

def test_unlisted_growing_log_is_reported(tmp_path):
    listed = tmp_path / "known.log"
    listed.write_bytes(b"a" * 100)
    stranger = tmp_path / "новый.log"
    stranger.write_bytes(b"b" * 3000)
    found = log_rotate.unlisted_logs(_conf(tmp_path, listed), min_mb=0.001)
    assert [r["path"] for r in found] == [str(stranger)], \
        "лог рядом с домом, не названный в конфиге, обязан быть виден"


def test_negative_control_listed_log_stays_silent(tmp_path):
    """Датчик, который кричит всегда, выучил бы себя игнорировать (§13)."""
    listed = tmp_path / "known.log"
    listed.write_bytes(b"a" * 9000)                  # ЖИРНЫЙ, но он в конфиге
    assert log_rotate.unlisted_logs(_conf(tmp_path, listed), min_mb=0.001) == []


def test_small_stranger_below_min_mb_is_not_noise(tmp_path):
    listed = tmp_path / "known.log"
    listed.write_bytes(b"a" * 100)
    (tmp_path / "мелкий.log").write_bytes(b"b" * 10)
    assert log_rotate.unlisted_logs(_conf(tmp_path, listed), min_mb=1.0) == []


def test_over_threshold_marks_log_past_rotation_size(tmp_path):
    """Порог живёт в конфиге и только там: size_kb=1 → 3 КБ уже перерос."""
    listed = tmp_path / "known.log"
    listed.write_bytes(b"a" * 100)
    (tmp_path / "жирный.log").write_bytes(b"b" * 3000)
    found = log_rotate.unlisted_logs(_conf(tmp_path, listed, size_kb=1), min_mb=0.001)
    assert found[0]["over_threshold"] is True
    found2 = log_rotate.unlisted_logs(_conf(tmp_path, listed, size_kb=9999), min_mb=0.001)
    assert found2[0]["over_threshold"] is False


def test_dead_conf_screams_instead_of_returning_empty(tmp_path):
    """Фальшивый зелёный: пустой конфиг неотличим от «всё чисто» (§14)."""
    c = tmp_path / "c.conf"
    c.write_text("# только комментарий\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="пуст или не разобран"):
        log_rotate.rotate_all(c)
    with pytest.raises(ValueError, match="пуст или не разобран"):
        log_rotate.unlisted_logs(c, min_mb=0.001)


# ── квитанция прогона: агент жив И самопроверка прошла ───────────────────────

def _receipt(tmp_path, text, age_h=0.0):
    import os, time
    r = tmp_path / "logrotate.log"
    r.write_text(text, encoding="utf-8")
    t = time.time() - age_h * 3600
    os.utime(r, (t, t))
    return r


def test_receipt_missing_is_not_silence(tmp_path):
    st = log_rotate.receipt_status(tmp_path / "нет.log")
    assert st == {"exists": False, "age_h": None, "selftest_ok": False}


def test_receipt_fresh_and_clean(tmp_path):
    st = log_rotate.receipt_status(_receipt(tmp_path, "[]\nselftest ok\n"))
    assert st["exists"] and st["selftest_ok"] and st["age_h"] < 0.1


def test_receipt_without_selftest_is_dirty(tmp_path):
    """Прогон был (mtime свежий), но самопроверка не дошла до конца — §14: живой ≠ рабочий."""
    st = log_rotate.receipt_status(_receipt(tmp_path, "[]\nTraceback…AssertionError\n"))
    assert st["exists"] and st["age_h"] < 0.1 and st["selftest_ok"] is False


def test_receipt_age_is_measured_not_assumed(tmp_path):
    st = log_rotate.receipt_status(_receipt(tmp_path, "selftest ok\n", age_h=30))
    assert 29.5 < st["age_h"] < 30.5


@pytest.mark.host_only
def test_receipt_from_thread_tree_points_to_main_copy(tmp_path):
    """21.09: pre-commit нити искал квитанцию в `logs/` дерева нити и печатал
    «агент не запускался» при живом агенте. Квитанция одна — в главной копии."""
    import subprocess
    main = tmp_path / "main"
    main.mkdir()
    g = lambda *a, cwd=main: subprocess.run(["git", *a], cwd=cwd, check=True,
                                            capture_output=True)
    g("init", "-q", "-b", "main")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
      "--allow-empty", "-m", "0")
    tree = tmp_path / "tree"
    g("worktree", "add", "-q", "-b", "thread/x", str(tree))
    want = main.resolve() / "logs" / "logrotate.log"
    assert log_rotate.default_receipt(tree) == want
    assert log_rotate.default_receipt(main) == want
    # не git — прежнее поведение, путь от кода
    plain = tmp_path / "plain"
    plain.mkdir()
    assert log_rotate.default_receipt(plain) == plain / "logs" / "logrotate.log"


def test_receipt_status_asks_main_copy_by_default(tmp_path, monkeypatch):
    """Проводка: без пути receipt_status спрашивает default_receipt, а не строит путь сам."""
    r = _receipt(tmp_path, "selftest ok\n")
    monkeypatch.setattr(log_rotate, "default_receipt", lambda start: r)
    assert log_rotate.receipt_status()["selftest_ok"] is True


# ── периметр конфига: плист объявляет лог, конфиг обязан его знать (02.09) ────

import re as _re
from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parents[2]
_DECL = log_rotate.DECLARED   # один дом правила — модуль ротации (28.09.2026)


def _declared_by_plists() -> set:
    """РЕКУРСИВНО, включая `launchd/<машина>/` (2026-09-07).

    Копии плистов разложены по владельцу: корень `launchd/` — Studio, подкаталог
    `launchd/macbook/` — MacBook. Ротация логов от машины НЕ зависит (`log_rotate`
    бежит на обеих, конфиг у них один дом), поэтому периметр здесь — ВСЕ копии.
    Это осознанно НЕ совпадает с областью `plist_env_liveness.repo_plist_drift`,
    которая смотрит только корень: сверять MacBook-джобы с живыми джобами Studio
    значит краснеть на здоровом (см. test_plist_scopes_differ_on_purpose)."""
    out = set()
    for pl in sorted((_ROOT / "launchd").rglob("*.plist")):
        out |= {_P(m) for m in _DECL.findall(pl.read_text(errors="ignore"))}
    return out


@pytest.mark.owner_data
def test_plist_scopes_differ_on_purpose():
    """⭐ Две области над одним каталогом РАЗНЫЕ, и это решение, а не случайность.

    Цена ошибки измерена в тот же день, когда конвенция заведена: 07.09 копия
    MacBook-джобы `com.larry.health.backup-wip` легла в корень `launchd/`, и ночной
    `check_launchd_inventory` (Studio-only) назвал её «копия есть, живой джобы нет» —
    ложный варн владельцу наутро, потому что на Studio этой джобы нет и быть не должно.

    Правило: корень `launchd/` — джобы Studio, `launchd/<машина>/` — чужие.
      · инвентарь живых джоб (`plist_env_liveness._load_plists`) — ТОЛЬКО корень;
      · периметр ротации логов (`_declared_by_plists`) — ВСЕ копии рекурсивно.
    Мутация в любую сторону роняет этот тест: сделаешь инвентарь рекурсивным — вернётся
    ложный варн; сделаешь ротацию нерекурсивной — MacBook-логи снова вырастут без
    предела и датчик слепого пятна их не увидит."""
    import plist_env_liveness as _pl
    root = _ROOT / "launchd"
    machine_copies = sorted(root.glob("*/*.plist"))
    assert machine_copies, ("позитивный контроль пуст: копий чужих машин нет, "
                            "и тест ничего не доказывает")
    inventory = set(_pl._load_plists(root))
    declared = _declared_by_plists()
    for pl in machine_copies:
        assert pl.stem not in inventory, (
            f"{pl.stem} попал в инвентарь живых джоб Studio — вернётся ложный варн "
            f"«копия есть, живой джобы нет»")
        assert any(_P(m) in declared for m in _DECL.findall(pl.read_text(errors="ignore"))), (
            f"логи {pl.stem} выпали из периметра ротации — обход перестал быть рекурсивным")


def test_every_declared_log_is_rotated():
    """Замер 02.09: 26 из 30 объявленных плистами логов были вне ротации.

    Датчик «лог вне конфига» их не видел — они мелкие СЕГОДНЯ, а растут без предела.
    Плист — это ДЕКЛАРАЦИЯ пути лога; конфиг ротации обязан её покрывать. Тот же приём,
    что в C-36: периметр берётся из места, где предмет объявлен, а не угадывается.
    """
    listed = {r["path"] for r in log_rotate._parse_conf(log_rotate._DEFAULT_CONF)}
    missing = sorted(str(p) for p in _declared_by_plists() if p not in listed)
    assert not missing, (
        f"плисты объявляют логи, которых нет в конфиге ротации: {missing}. "
        "Допиши строки в launchd/health-logs.newsyslog.conf — иначе лог растёт без предела, "
        "а датчик слепого пятна его не увидит (он смотрит только каталоги из конфига).")


@pytest.mark.owner_data
def test_declared_perimeter_is_not_empty():
    """Негативный контроль против вечно-зелёного: пустой периметр = слепой сторож."""
    assert len(_declared_by_plists()) >= 10, "плистов не нашлось — сторож проверяет пустоту"
