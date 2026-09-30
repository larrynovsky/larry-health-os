"""Характеризационные тесты границы двух хранилищ. Каждый предикат проверяется отдельно позитивным и негативным примером. Все строки независимо сконструированы."""
import pytest

from migrations import canon_domain_leak_20260801 as m

VERDICTS = {"microbiome": "specialized", "trace_elements": "canon",
            "chemistry": "canon"}


def _c(name, specimen="blood", date="2040-04-09", source="doc:x.pdf", rid=1):
    return {"id": rid, "date": date, "source": source, "specimen": specimen,
            "test_name": name, "value": 1.0, "unit": "mg/L", "method": None}


def _s(raw, panel_type, specimen="blood", date="2040-04-09", source="doc:x.pdf",
       canonical=None, rid=1):
    return {"id": rid, "date": date, "source": source, "specimen": specimen,
            "panel_type": panel_type, "analyte_raw": raw,
            "analyte_canonical": canonical, "value": 1.0, "unit": "mg/L",
            "method": None}


def _stg(raw, panel, specimen_raw=None, date="2040-04-09", src="x.pdf"):
    return {"date": date, "source_file": src, "raw_name": raw,
            "canonical_name": None, "panel": panel, "specimen": specimen_raw,
            "specimen_source": "read_header" if specimen_raw else "unknown",
            "value": 1.0, "unit": "mg/L", "ref_low": None, "ref_high": None,
            "doc_flag": None, "method": None, "method_source": None,
            "page_role": "data"}


# ── A. выселение из канона ────────────────────────────────────────────────────

def test_A_evicts_only_rows_with_a_twin():
    """Позитив: строка чужого дома с двойником в спец-слое — на выселение."""
    canon = [_c("Bifidobacterium spp.")]
    spec = [_s("Bifidobacterium spp.", "microbiome")]
    stg = [_stg("Bifidobacterium spp.", "microbiome", specimen_raw="Кровь с ЭДТА")]
    evict, orphan = m.plan_evict_from_canon(canon, spec, stg, VERDICTS)
    assert len(evict) == 1 and orphan == []


def test_A_orphan_is_never_deleted():
    """НЕГАТИВНЫЙ КОНТРОЛЬ и главный риск работы (R1).

    Строка, у которой двойника НЕТ, удалению не подлежит: это не чистка дубля,
    а тихая потеря единственного экземпляра измерения. Здесь двойник есть
    в спец-слое под ДРУГИМ материалом — то есть это другое измерение.
    """
    canon = [_c("Bifidobacterium spp.", specimen="stool")]
    spec = [_s("Bifidobacterium spp.", "microbiome", specimen="throat_swab")]
    stg = [_stg("Bifidobacterium spp.", "microbiome", specimen_raw="Кал")]
    evict, orphan = m.plan_evict_from_canon(canon, spec, stg, VERDICTS)
    assert evict == [] and len(orphan) == 1


def test_A_silent_on_rows_whose_home_is_canon():
    """Негатив: класс с домом «канон» из канона не выселяется."""
    canon = [_c("Ртуть, Hg")]
    spec = [_s("Ртуть, Hg", "trace_elements")]
    stg = [_stg("Ртуть, Hg", "other", specimen_raw="Кровь с ЭДТА")]
    evict, orphan = m.plan_evict_from_canon(canon, spec, stg, VERDICTS)
    assert evict == [] and orphan == []


def test_A_silent_on_class_without_verdict():
    """Класс, которого человек не судил, миграция не трогает вовсе."""
    canon = [_c("Мюоны, µ")]
    spec = [_s("Мюоны, µ", "cosmic_rays")]
    stg = [_stg("Мюоны, µ", "cosmic_rays", specimen_raw="Кровь с ЭДТА")]
    evict, orphan = m.plan_evict_from_canon(canon, spec, stg, VERDICTS)
    assert evict == [] and orphan == []


# ── B. выселение из спец-слоя ────────────────────────────────────────────────

def test_B_evicts_when_canon_already_has_it():
    canon = [_c("Ртуть, Hg")]
    spec = [_s("Ртуть, Hg", "trace_elements")]
    assert len(m.plan_evict_from_specialized(canon, spec, VERDICTS)) == 1


def test_B_matches_canonical_name_too():
    """Переименованная каноническая строка остаётся тем же измерением. Синтетический двойник проверяет canonical_name."""
    canon = [_c("Magnesium")]
    spec = [_s("Магний, Mg", "trace_elements", canonical="Magnesium")]
    assert len(m.plan_evict_from_specialized(canon, spec, VERDICTS)) == 1


def test_B_does_not_evict_what_canon_lacks():
    """Негатив: без двойника в каноне выселение = потеря."""
    canon = []
    spec = [_s("Ртуть, Hg", "trace_elements")]
    assert m.plan_evict_from_specialized(canon, spec, VERDICTS) == []


def test_B_blood_and_urine_are_two_measurements():
    """Ключ включает материал: кровь и моча не являются дублями даже при совпадении имени."""
    canon = [_c("Цинк, Zn", specimen="blood")]
    spec = [_s("Цинк, Zn", "trace_elements", specimen="urine")]
    assert m.plan_evict_from_specialized(canon, spec, VERDICTS) == []


# ── C. перенос в канон ────────────────────────────────────────────────────────

def test_C_reports_worklist_with_staging_provenance():
    """Worklist берёт родителя из staging с его актуальными полями. Это отчёт, а решение о записи остаётся у писателя канона."""
    canon = []
    spec = [_s("Ртуть, Hg", "trace_elements")]
    stg = [_stg("Ртуть, Hg", "other", specimen_raw="Кровь с ЭДТА")]
    insert, unsourced = m.plan_move_to_canon(canon, spec, stg, VERDICTS)
    assert len(insert) == 1 and unsourced == []
    assert insert[0]["_src"] == "doc:x.pdf" and insert[0]["_spec"] == "blood"


def test_C_row_without_a_parent_in_staging_does_not_travel():
    """НЕГАТИВНЫЙ КОНТРОЛЬ: нет родителя — нет переноса, есть отдельный список."""
    canon = []
    spec = [_s("Ртуть, Hg", "trace_elements")]
    insert, unsourced = m.plan_move_to_canon(canon, spec, [], VERDICTS)
    assert insert == [] and len(unsourced) == 1


def test_C_does_not_duplicate_what_canon_already_has():
    """Негатив: строка уже в каноне — это случай B, не C."""
    canon = [_c("Ртуть, Hg")]
    spec = [_s("Ртуть, Hg", "trace_elements")]
    stg = [_stg("Ртуть, Hg", "other", specimen_raw="Кровь с ЭДТА")]
    insert, _u = m.plan_move_to_canon(canon, spec, stg, VERDICTS)
    assert insert == []


def test_C_ignores_derived_chart_pages():
    """Страница-график («Диаграмма дисбиоза» = Проба − Норма) — не измерение.
    Родителем для переноса она быть не может."""
    canon = []
    spec = [_s("Ртуть, Hg", "trace_elements")]
    chart = dict(_stg("Ртуть, Hg", "other", specimen_raw="Кровь с ЭДТА"),
                 page_role="derived_chart")
    insert, unsourced = m.plan_move_to_canon(canon, spec, [chart], VERDICTS)
    assert insert == [] and len(unsourced) == 1


def test_run_is_dry_by_default(tmp_path, monkeypatch):
    """Публичный вход по умолчанию не мутирует ничего."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, unit TEXT, ref_low REAL, ref_high REAL,
            status TEXT, specimen TEXT, method TEXT)""")
        c.commit()
    r = m.run()
    assert r["applied"] is False


def test_run_never_inserts_into_canon(tmp_path, monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ на второй путь записи: применение НЕ добавляет строк
    в канон ни при каких данных. Единственная дверь в канон — `lab_promote`."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, unit TEXT, ref_low REAL, ref_high REAL,
            status TEXT, specimen TEXT, method TEXT)""")
        c.execute("INSERT INTO specialized_lab_results"
                  "(date,source,panel_type,specimen,analyte_raw,value,unit) "
                  "VALUES('2040-04-09','doc:x.pdf','trace_elements','blood','Ртуть, Hg',1.0,'мкг/л')")
        c.execute("INSERT INTO lab_results_staging"
                  "(run_id,extractor_version,source_file,date,panel,raw_name,value,"
                  "specimen,specimen_source) VALUES('r','v','x.pdf','2040-04-09',"
                  "'other','Ртуть, Hg',1.0,'Кровь с ЭДТА','read_header')")
        c.commit()
    before = m.run()
    assert before["C_worklist_not_in_canon"] == 1
    m.run(apply=True)
    with health_db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0] == 0


# ── B/B2: идентичность имя+материал+размерность (2026-08-12) ─────────────────

def test_B_matches_resolved_identity():
    """Синтетические сырое и каноническое имена совпадают после нормализации."""
    canon = [dict(_c("Ferritin"), unit="ng/ml")]
    spec = [dict(_s("Ферритин", "chemistry"), unit="ng/ml")]
    assert len(m.plan_evict_from_specialized(canon, spec, VERDICTS)) == 1


def test_B2_evicts_cross_source_value_equal():
    """Независимо придуманные документ и перевод: одна идентичность и равные значения обозначают дубль."""
    canon = [dict(_c("Ferritin", source="doc:translation.pdf"),
                  unit="ng/ml", value=12.8)]
    spec = [dict(_s("Ферритин", "chemistry", source="doc:original.pdf"),
                 unit="ng/ml", value=12.8)]
    out, mism = m.plan_evict_cross_source_value_equal(canon, spec, VERDICTS)
    assert len(out) == 1 and mism == []


def test_B2_value_mismatch_is_untouched():
    """НЕГАТИВНЫЙ КОНТРОЛЬ (R3): идентичность совпала, значение НЕТ — пересдача
    тем же днём в другой лаборатории законна. Не удаляется, а печатается."""
    canon = [dict(_c("Ferritin", source="doc:other-lab.pdf"),
                  unit="ng/ml", value=37.2)]
    spec = [dict(_s("Ферритин", "chemistry", source="doc:original.pdf"),
                 unit="ng/ml", value=12.8)]
    out, mism = m.plan_evict_cross_source_value_equal(canon, spec, VERDICTS)
    assert out == [] and len(mism) == 1


def test_B2_insulin_percent_guard_holds():
    """Гард голого %: антитело «Инсулин −3 %» не признаётся дублем гормона
    Insulin (мкМЕ/мл) даже при равном числе — identity_name(%-строки) = None,
    сравнивать нечем. Краснеет при снятии гарда (исполняемый негативный контроль)."""
    canon = [dict(_c("Insulin", source="doc:b.pdf"), unit="мкМЕ/мл", value=7.0)]
    spec = [dict(_s("Инсулин", "chemistry", source="doc:a.pdf"),
                 unit="%", value=7.0)]
    out, mism = m.plan_evict_cross_source_value_equal(canon, spec, VERDICTS)
    assert out == [] and mism == []


def test_B2_same_source_stays_in_B_not_B2():
    """B2 — про ДРУГОЙ источник; same-source дубль остаётся случаем B (в run()
    списки дедуплицируются по id, двойного удаления нет)."""
    canon = [dict(_c("Ferritin", source="doc:x.pdf"), unit="ng/ml", value=12.8)]
    spec = [dict(_s("Ферритин", "chemistry", source="doc:x.pdf"),
                 unit="ng/ml", value=12.8)]
    out, _m2 = m.plan_evict_cross_source_value_equal(canon, spec, VERDICTS)
    assert out == []


def test_B2_no_value_no_eviction():
    """Строка без числа (качественный результат) кросс-источником не судится."""
    canon = [dict(_c("Ferritin", source="doc:t.pdf"), unit="ng/ml", value=12.8)]
    spec = [dict(_s("Ферритин", "chemistry", source="doc:o.pdf"),
                 unit="ng/ml", value=None)]
    out, mism = m.plan_evict_cross_source_value_equal(canon, spec, VERDICTS)
    assert out == [] and mism == []


def test_B_literal_name_with_diverging_identity_is_not_a_double():
    """Синтетические концентрация и доля имеют общее имя, но разные идентичности. Буквальный матч не должен удалить одну из них."""
    canon = [dict(_c("Albumin"), unit="г/л", value=42.1)]
    spec = [dict(_s("Albumin", "chemistry"), unit="%", value=60.2)]
    assert m.plan_evict_from_specialized(canon, spec, VERDICTS) == []
    # настоящий дубль (та же идентичность) по-прежнему эвиктится
    canon2 = [dict(_c("Albumin"), unit="г/л", value=42.1)]
    spec2 = [dict(_s("Albumin", "chemistry"), unit="г/л", value=42.1)]
    assert len(m.plan_evict_from_specialized(canon2, spec2, VERDICTS)) == 1
