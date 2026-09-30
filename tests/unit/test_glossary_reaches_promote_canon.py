"""F-14, вторая половина: `_canon` действительно читает глоссарий — и не всякий.

Главный контроль — НЕГАТИВНЫЙ: цель, которой канон не знает, принимать нельзя.
Замер 31.07: из 54 целей глоссария ровно одна такая (`UricAcid` при каноническом
`Uric_acid`), и приняв её на веру, мы расщепили бы тренд мочевой кислоты молча.
"""
import health_db  # noqa: F401 — ПЕРВЫМ: циркулярный импорт
import lab_canon
import lab_promote


def _row(raw, model=None):
    return {"raw_name": raw, "canonical_name": model}


def test_подтверждение_человека_старше_словаря_в_коде():
    known = next(iter(lab_canon.CANONICALS))
    assert lab_promote._canon(_row("невиданное имя"), {"невиданное имя": known}) == known


def test_цель_неизвестная_канону_НЕ_принимается():
    """Без этой ветки `UricAcid` из глоссария стал бы вторым именем `Uric_acid`."""
    got = lab_promote._canon(_row("harnsäure i.s."), {"harnsäure i.s.": "UricAcid"})
    assert got != "UricAcid"


def test_глоссарий_сверяется_по_нижнему_регистру():
    known = next(iter(lab_canon.CANONICALS))
    assert lab_promote._canon(_row("  Harnstoff I.S.  "),
                              {"harnstoff i.s.": known}) == known


def test_пустой_глоссарий_прежнее_поведение_не_меняет():
    """Позитивный контроль обратной совместимости: без глоссария лестница
    работает ровно как до правки."""
    assert lab_promote._canon(_row("Гемоглобин"), {}) == lab_promote._canon(
        _row("Гемоглобин"), None)


def test_промоут_не_спрашивает_несуществующий_формат():
    """Ратчет на первую половину F-14. Судится КОД, а не комментарии: литерал
    остался в объяснении «как было», и ратчет обязан их различать."""
    import pathlib
    src = (pathlib.Path(lab_promote.__file__)).read_text()
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "get_confirmed_aliases(0)" not in code, \
        "промоут снова спрашивает несуществующий формат 0 — F-14 вернулся"
    assert "get_confirmed_aliases_all()" in code
