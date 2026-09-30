"""Критерий переезда профиля органических кислот мочи из канона в свой дом.

Акт разрушающий: строка удаляется из `lab_results`, и ошибка отбора хоронит живое
измерение молча. Поэтому проверяется КРИТЕРИЙ (кого берёт, кого не трогает), а не
арифметика, которая из него следует.

Три границы, каждая своим тестом:
  · строка профиля с сырым именем — переезжает;
  · строка профиля, у которой каноническое имя УЖЕ есть, — остаётся (это названная
    граница решения владельца 14.09, и без теста она держалась бы на честном слове);
  · строка с другой единицей — не тронута, даже если имя похоже.
"""
from __future__ import annotations

import pytest

from migrations import metabolomics_home_20260914 as act

pytestmark = pytest.mark.unit

UNIT = act.PANEL_UNIT


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    # `init_db` таблицу канона НЕ создаёт (проверено замером ещё 08.08) — её строит
    # общий дом схемы из conftest, теми же продакшн-функциями, что на живой БД.
    from tests.conftest import canon_schema
    canon_schema(health_db)
    return health_db


def _add(conn, name, unit=UNIT):
    conn.execute(
        "INSERT INTO lab_results (date, source, specimen, test_name, value, unit) "
        "VALUES ('2021-06-14', 'doc:x.pdf', 'urine', ?, 1.0, ?)", (name, unit))


def test_raw_profile_rows_move(db):
    """Сырые имена профиля — и кислоты, и ацилглицины — попадают в план."""
    with db.get_conn() as c:
        _add(c, "2-Кетоглутаровая кислота (2-оксоглутаровая кислота)")
        _add(c, "3-Метилкротонилглицин")
        c.commit()
        names = {r["test_name"] for r in act.plan_move(c)}
    assert names == {"2-Кетоглутаровая кислота (2-оксоглутаровая кислота)",
                     "3-Метилкротонилглицин"}


def test_row_with_a_canonical_name_stays_in_canon(db):
    """⭐ Граница решения: у метилмалоновой кислоты мочи каноническое имя есть,
    она маркер B12 и остаётся в каноне. Без этого теста граница держится на слове."""
    with db.get_conn() as c:
        _add(c, "Urine_Methylmalonic_acid")
        c.commit()
        assert act.plan_move(c) == []


def test_other_units_are_never_touched(db):
    """Единица — половина критерия: имя с «кислота» вне профиля не переезжает.
    Без этого теста предикат зеленел бы и на правиле «бери всё, что кислота»."""
    with db.get_conn() as c:
        _add(c, "Фолиевая кислота", unit="нг/мл")
        _add(c, "Мочевая кислота", unit="мкмоль/л")
        c.commit()
        assert act.plan_move(c) == []


def test_apply_fixes_the_verdict_on_a_live_base(db, monkeypatch):
    """⭐ Акт правит ВЕРДИКТ, а не только строки.

    Seed стоит под INSERT OR IGNORE и живую строку не трогает — так и задумано,
    иначе правка человека не пережила бы рестарт. Значит на базе, которая знала
    класс как `canon`, переезд без UPDATE оставил бы строки в спец-слое при
    вердикте «дом — канон», то есть сам создал бы находку датчика границы.
    Здесь это воспроизведено: вердикт возвращён в `canon`, как на живой базе."""
    import labs_db
    with db.get_conn() as c:
        c.execute("UPDATE lab_domain_verdicts SET home='canon' WHERE panel_type='metabolomics'")
        _add(c, "2-Кетоглутаровая кислота")
        c.commit()
    monkeypatch.setattr("sys.argv", ["m", "--apply"])
    assert act.main() == 0
    with db.get_conn() as c:
        assert labs_db.domain_home("metabolomics", conn=c) == "specialized"
        assert act.plan_move(c) == [], "строка осталась в каноне"
        moved = c.execute("SELECT count(*) FROM specialized_lab_results "
                          "WHERE panel_type='metabolomics'").fetchone()[0]
    assert moved == 1


def test_verdict_says_specialized(db):
    """Seed несёт решение владельца: без него переезд был бы правкой мимо вердикта."""
    import labs_db
    with db.get_conn() as c:
        assert labs_db.domain_home("metabolomics", conn=c) == "specialized"
        # Позитивный контроль: соседний класс дом не сменил.
        assert labs_db.domain_home("amino_acids", conn=c) == "canon"
