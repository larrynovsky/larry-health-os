"""tests co-draw (conit-группировка). draw_key — pure, но labs_db импортит health_db
(circular), поэтому импорт через фасад. build_codraw_context — integration (нужна БД),
покрыт живой валидацией на Studio (plan Фаза 1); планово-посаженные кейсы — follow-up
(требуют выноса acute-логики в чистую функцию)."""
import health_db  # noqa: F401 — резолвит circular labs_db<->health_db
import labs_db


def test_draw_key_merges_prefix_variants():
    # один документ под doc:CR/ и голым именем на одной дате → один забор
    a = labs_db.draw_key("doc:CR/10000000001.PDF", "2026-05-30")
    b = labs_db.draw_key("10000000001.PDF", "2026-05-30")
    assert a == b == ("10000000001.PDF", "2026-05-30")


def test_draw_key_separates_reused_filename_by_date():
    # одинаковое имя файла в разных папках/датах → разные заборы (не ложный merge)
    a = labs_db.draw_key("doc:inbox/A/REPORT.pdf", "2020-03-02")
    b = labs_db.draw_key("doc:inbox/B/REPORT.pdf", "2021-04-19")
    assert a != b


def _row(tn, v, d, src, lo, hi, unit=""):
    return {"test_name": tn, "value": v, "unit": unit, "date": d,
            "source": src, "ref_low": lo, "ref_high": hi}


def test_codraw_acute_cluster_fires():
    # WBC и Glucose ВПЕРВЫЕ вне нормы на 05-30 (предыдущие в норме) → кластер из 2
    rows = [
        _row("WBC", 8.0, "2026-05-24", "d1.pdf", 4.0, 11.0),
        _row("Glucose", 90, "2026-05-24", "d1.pdf", 70, 100),
        _row("WBC", 15.0, "2026-05-30", "d2.pdf", 4.0, 11.0),
        _row("Glucose", 130, "2026-05-30", "d2.pdf", 70, 100),
    ]
    cl = labs_db._codraw_clusters(rows, "2026-05-01", min_cluster=2)
    assert len(cl) == 1 and cl[0][1] == "2026-05-30" and len(cl[0][2]) == 2


def test_codraw_chronic_excluded():
    # RDW хронически вне (обе даты) → не острое; остаётся один острый WBC < min_cluster
    rows = [
        _row("RDW", 16.0, "2026-05-24", "d1.pdf", 11, 14),
        _row("RDW", 16.5, "2026-05-30", "d2.pdf", 11, 14),
        _row("WBC", 15.0, "2026-05-30", "d2.pdf", 4, 11),
    ]
    assert labs_db._codraw_clusters(rows, "2026-05-01", min_cluster=2) == []


def test_codraw_prefix_variants_one_draw():
    # тот же документ под doc:CR/ и голым именем на одной дате → ОДИН забор, 2 острых
    rows = [
        _row("WBC", 8.0, "2026-05-24", "old.pdf", 4, 11),
        _row("Glucose", 90, "2026-05-24", "old.pdf", 70, 100),
        _row("WBC", 15.0, "2026-05-30", "doc:CR/x.pdf", 4, 11),
        _row("Glucose", 130, "2026-05-30", "x.pdf", 70, 100),
    ]
    cl = labs_db._codraw_clusters(rows, "2026-05-01", min_cluster=2)
    assert len(cl) == 1 and cl[0][0] == "x.pdf"


def test_codraw_one_sided_ref_does_not_crash_and_points_arrow():
    """Вымышленная пара с односторонними референсами пересекает обе границы.
    Отсутствующая вторая граница не должна приводить к float(None)."""
    rows = [
        _row("HDL", 72, "2032-02-09", "d1.pdf", 60, None),
        _row("HbA1c", 5.3, "2032-02-09", "d1.pdf", None, 5.7),
        _row("HDL", 47, "2032-03-18", "d2.pdf", 60, None),
        _row("HbA1c", 6.6, "2032-03-18", "d2.pdf", None, 5.7),
    ]
    cl = labs_db._codraw_clusters(rows, "2032-01-01", min_cluster=2)
    assert len(cl) == 1 and cl[0][1] == "2032-03-18"
    abn = " | ".join(cl[0][2])
    hdl = next(x for x in cl[0][2] if x.startswith("HDL"))
    a1c = next(x for x in cl[0][2] if "6.6" in x)
    assert "↓" in hdl and "≥60" in hdl and "↑" in a1c and "≤5.7" in a1c, abn


def test_codraw_no_ref_at_all_is_unknown_not_abnormal():
    # обе границы NULL → не судим (None), не «вне нормы»
    rows = [
        _row("X", 5, "2032-03-18", "d.pdf", None, None),
        _row("Y", 5, "2032-03-18", "d.pdf", None, None),
    ]
    assert labs_db._codraw_clusters(rows, "2032-01-01", min_cluster=2) == []
