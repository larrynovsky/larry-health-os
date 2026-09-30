"""Характеризация affected_claims + ИСПОЛНЕННЫЙ негативный контроль.

Негативный контроль здесь — не украшение, а единственное доказательство, что
зелёный причинён МЕТКОЙ, а не совпадением (§20). Позитив «правка backup.sh
выбирает §3» прошёл бы и у механизма, который выбирает §3 всегда.
"""
import re
from pathlib import Path

import pytest

import affected_claims as ac

ROOT = Path(__file__).resolve().parents[2]
# Свод в закрытой части (28.09): в открытой выгрузке его нет, тесты на нём — owner_data.
SVOD = (ROOT / "CLAUDE.md").read_text(encoding="utf-8") if (ROOT / "CLAUDE.md").exists() else ""


@pytest.mark.owner_data
def test_touched_carrier_selects_its_claim():
    """`backup.sh` — носитель ДВУХ утверждений: §3 (бэкап перед destructive) и, с
    2026-09-07, §1 (снимок дерева каждые 3ч в refs/backups/wip). Ожидание было {§3}
    и обновлено, когда факт изменился: тест утверждает про СВОД, а причинность
    самого отбора держит негативный контроль ниже."""
    hits, errors = ac.touched(["backup.sh"], text=SVOD, root=ROOT)
    assert errors == []
    assert {h.claim.section for h in hits} == {"§1", "§3"}
    assert all(h.carrier == "backup.sh" for h in hits)


@pytest.mark.owner_data
def test_negative_control_metka_removed_kills_the_hit():
    """МУТАЦИЯ: у §3 стёрта метка. Тот же вход обязан перестать выбирать §3.
    Не перестал — значит выбор шёл не по метке, и позитив выше ничего не доказывал.
    §1 остаётся: он несёт `backup.sh` своей меткой, и его выживание показывает, что
    исчез именно мутированный носитель, а не отбор целиком."""
    stripped = re.sub(r" ⟨carriers: backup_studio\.sh, backup\.sh⟩", "", SVOD)
    assert stripped != SVOD, "мутация не применилась — контроль был бы пустым"
    hits, errors = ac.touched(["backup.sh"], text=stripped, root=ROOT)
    assert errors == []
    assert {h.claim.section for h in hits} == {"§1"}


@pytest.mark.owner_data
def test_untouched_file_selects_nothing():
    hits, errors = ac.touched(["README.md"], text=SVOD, root=ROOT)
    assert (hits, errors) == ([], [])


def test_carrier_with_symbol_resolves_and_selects_by_file(tmp_path):
    """Носитель вида «файл::символ»: существование проверяется в ДВА шага, а
    отбор идёт по файловой части — git отдаёт пути без символов.

    Форма появилась в своде 2026-09-11 (§15), механизм её не знал: искал файл с
    именем «...py::perimeter_policy», краснел на верной метке и одновременно был
    слеп — ни один изменённый путь с такой строкой не совпадал."""
    (tmp_path / "mod.py").write_text("def alive():\n    return 1\n", encoding="utf-8")
    svod = ("**§99 — норма.**\n"
            "*Enforcement:* **LIVE** — ⟨carriers: mod.py::alive⟩\n")
    hits, errors = ac.touched(["mod.py"], text=svod, root=tmp_path)
    assert errors == []
    assert [h.claim.section for h in hits] == ["§99"]
    assert hits[0].carrier == "mod.py::alive"


def test_renamed_symbol_is_red_not_silent(tmp_path):
    """Негативный контроль: символ переименован — адрес протух, механизм краснеет.
    Без этого поддержка «файл::символ» была бы декорацией: файл-то на месте."""
    (tmp_path / "mod.py").write_text("def renamed():\n    return 1\n", encoding="utf-8")
    svod = ("**§99 — норма.**\n"
            "*Enforcement:* **LIVE** — ⟨carriers: mod.py::alive⟩\n")
    _, errors = ac.touched(["mod.py"], text=svod, root=tmp_path)
    assert errors and "символ не найден" in errors[0]


def test_missing_file_still_red_with_symbol_form(tmp_path):
    """Файла нет вовсе — сообщение прежнее («носитель не найден»), не про символ."""
    svod = "*Enforcement:* **LIVE** — ⟨carriers: nope.py::alive⟩\n"
    _, errors = ac.touched([], text=svod, root=tmp_path)
    assert errors and "носитель не найден" in errors[0]


def test_unclosed_metka_is_error_not_silence():
    claims, errors = ac.parse_claims("бла ⟨carriers: a.py\nследующая строка")
    assert claims == []
    assert errors and "не закрыта" in errors[0]


def test_empty_metka_is_error_not_silence():
    claims, errors = ac.parse_claims("бла ⟨carriers:   ⟩")
    assert claims == []
    assert errors and "пуста" in errors[0]


def test_carrier_absent_on_disk_is_error(tmp_path):
    """Протухший адрес обязан краснеть: иначе механизм зеленеет, ничего не стерегая."""
    (tmp_path / "CLAUDE.md").write_text("**§9** — норма ⟨carriers: нет-такого.py⟩",
                                        encoding="utf-8")
    hits, errors = ac.touched(["нет-такого.py"], root=tmp_path)
    assert hits, "связь по метке обязана строиться независимо от существования файла"
    assert errors and "не найден" in errors[0]


def test_section_binds_to_nearest_heading_above():
    text = "**§8** — заголовок\nпроза\n*Enforcement:* x ⟨carriers: a.py⟩"
    claims, errors = ac.parse_claims(text)
    assert errors == []
    assert claims[0].section == "§8" and claims[0].line == 3


def test_no_heading_above_falls_back_to_line_number():
    claims, _ = ac.parse_claims("одинокая строка ⟨carriers: a.py⟩")
    assert claims[0].section == "L1"


# ── unmarked-live: новая норма обещает LIVE, но носителя не называет ──────────

_DIFF_BARE = """@@ -100,0 +101 @@
+*Enforcement:* **LIVE** — `новый_сторож.py` ловит вот это
"""
_DIFF_MARKED = """@@ -100,0 +101 @@
+*Enforcement:* **LIVE** — `новый_сторож.py` ловит ⟨carriers: новый_сторож.py⟩
"""
_DIFF_REMOVED = """@@ -100 +0,0 @@
-*Enforcement:* **LIVE** — `старый.py` ловил вот это
"""


def test_added_live_without_metka_is_reported():
    out = ac.unmarked_live_added(_DIFF_BARE)
    assert len(out) == 1 and "новый_сторож.py" in out[0]


def test_negative_control_metka_present_silences_it():
    """МУТАЦИЯ входа: та же строка С меткой обязана дать пусто.
    Иначе датчик кричит всегда и учит себя игнорировать (§13)."""
    assert ac.unmarked_live_added(_DIFF_MARKED) == []


def test_removed_line_is_not_reported():
    """Легаси не красится: судим добавленное, а не всё, что мелькнуло в диффе."""
    assert ac.unmarked_live_added(_DIFF_REMOVED) == []


def test_diff_header_lines_are_not_mistaken_for_content():
    assert ac.unmarked_live_added("+++ b/CLAUDE.md\n") == []


@pytest.mark.owner_data   # читает CLAUDE.md — закрытая часть (28.09)
def test_coverage_counts_marked_over_total():
    have, total = ac.live_coverage(SVOD)
    assert total >= 12, "строк LIVE в своде стало меньше — сверь, не потерялась ли норма"
    assert 0 < have <= total


def test_open_export_without_svod_selects_nothing(tmp_path):
    """Открытая выгрузка (28.09): CLAUDE.md в закрытой части — выбирать нечего, это не ошибка."""
    assert ac.touched(["backup.sh"], root=tmp_path) == ([], [])
