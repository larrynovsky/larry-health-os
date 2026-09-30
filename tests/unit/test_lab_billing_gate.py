"""Тесты Layer-1/Layer-2 гейтов против утечки инвойсов в лаб-пайплайн (#67, 2026-07-02).

Инцидент: счета/чеки под CR/Payments уходили в vision через lab_reconcile →
в канон попадали количества (1.0 в графе аналита), цены (лекарство=цена€), процедуры
(операция). Гейты: (L1) lab_reconcile не сканирует биллинг-доки;
(L2) промоут не пускает валютные единицы и не-аналитные имена крови.
Канон НЕ трогаем — dry-run + временная БД.
"""
import pytest


# ── Layer 2: чистая функция _block_reason ─────────────────────────────────────

def _row(**kw):
    # `_home`/`_class` проставляет `prepare` (работа B, 2026-08-01): доменный гейт
    # стоит ПЕРЕД гейтами этого файла, и без вердикта строка не доходит до них.
    # Здесь дом фиксируем каноном намеренно — предмет тестов ниже другой.
    base = {"unit": None, "_specimen": "blood", "_cname": "", "canonical_name": None,
            "value": None, "_class": "chemistry", "_home": "canon"}
    # Строка БЕЗ явного числа несёт качественный результат (2026-08-08). Прежде здесь
    # был голый `value: None` — случайность фикстуры, безобидная до появления
    # универсального правила «строка обязана иметь результат». После него None стал
    # самостоятельной причиной блока и заслонял ПРЕДМЕТ этих тестов: маршрутизацию
    # (валюта, не-аналит, материал). Числовым умолчанием не отделаться — правдоподобие
    # зависит от аналита: 90 нормально для глюкозы и невозможно для гемоглобина, 5 —
    # наоборот. Качественный результат правдоподобен для любого имени и не будит
    # числовые оракулы (`if v is not None`), поэтому предмет остаётся предметом.
    if "value" not in kw:
        base["value_text"], base["_vtext"] = "отрицательно", "negative"
    base.update(kw)
    return base


def test_block_impossible_value():
    import lab_promote
    # известный аналит, но физически невозможное значение (инвойс-количество Amylase=2/Iron=1)
    assert lab_promote._block_reason(_row(_cname="Amylase", value=2.0)) == "impossible-value"
    assert lab_promote._block_reason(_row(_cname="Iron", value=1.0)) == "impossible-value"
    # нормальные значения того же аналита проходят
    assert lab_promote._block_reason(_row(_cname="Amylase", value=94.0, unit="U/L")) is None
    assert lab_promote._block_reason(_row(_cname="Iron", value=107.0, unit="ug/dL")) is None


def test_block_currency_unit():
    import lab_promote
    assert lab_promote._block_reason(_row(unit="€", _cname="DRUG_X")) == "currency-unit"
    assert lab_promote._block_reason(_row(unit="USD", _cname="PET-CT")) == "currency-unit"


def test_block_blood_nonanalyte():
    import lab_promote
    # процедура/услуга как «кровяной аналит» — не сводится к канону
    assert lab_promote._block_reason(_row(_cname="Procedure")) == "unmapped-nonanalyte"
    assert lab_promote._block_reason(_row(_cname="BLOOD UNIT, price per one unit")) == "unmapped-nonanalyte"


def test_allow_known_blood_analyte():
    import lab_promote
    # реальные аналиты крови проходят
    assert lab_promote._block_reason(_row(_cname="Ferritin")) is None
    assert lab_promote._block_reason(_row(_cname="Glucose")) is None
    assert lab_promote._block_reason(_row(_cname="Hemoglobin")) is None  # синоним → HGB


def test_allow_nonblood_specimen():
    import lab_promote
    # не-кровь доверяем модели (canonical_name), даже если имя не в CANONICALS
    assert lab_promote._block_reason(_row(_specimen="urine", _cname="Urine_Glucose")) is None
    assert lab_promote._block_reason(
        _row(_specimen="blood", canonical_name="Urine_Protein", _cname="Urine_Protein")) is None
    assert lab_promote._block_reason(_row(_specimen="stool", _cname="Calprotectin")) is None


# ── Layer 1: lab_reconcile пропускает биллинг-доки ────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS imported_docs(
            source_file TEXT, doc_type TEXT)""")
        c.executemany("INSERT INTO imported_docs(source_file, doc_type) VALUES(?,?)", [
            ("HOSPITAL.pdf", "hospital_bill"),     # биллинг → пропустить
            ("doctor_letter.pdf", "general_medical"),  # реальный не-lab → кандидат
            ("cbc.pdf", "lab"),                    # уже lab-путь → не кандидат
            ("scan.jpg", ""),                      # пустой тип → кандидат
        ])
        c.commit()
    return health_db


def test_candidates_excludes_billing(db):
    import lab_reconcile
    cands = lab_reconcile._candidates()
    assert "HOSPITAL.pdf" not in cands       # биллинг исключён
    assert "cbc.pdf" not in cands            # lab-тип исключён (идёт своим путём)
    assert "doctor_letter.pdf" in cands          # реальный не-lab сканируется
    assert "scan.jpg" in cands               # пустой тип сканируется


# ── Layer 2: интеграция через plan() (dry-run) ────────────────────────────────

@pytest.fixture
def db_promote(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    # Схема канона — ПРОДАКШН-функциями (tests/conftest.py::canon_schema), а не копией
    # DDL здесь: копия разъезжается с продом молча (замер 2026-08-08 — value_text).
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
                "raw_name,value,unit,value_agreement,review_status")
        c.executemany(f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            # реальный аналит крови → промоут
            ("r9", "v", "lab.pdf", "2025-06-03", "chemistry", "Glucose", "Glucose", 100.0, "mg/dL", "agree", "auto"),
            # цена в валюте → блок
            ("r9", "v", "HOSPITAL.pdf", "2020-01-01", "", "DRUG_X", "DRUG_X", 1200.0, "€", "single", "auto"),
            # процедура (кровяной not-analyte) → блок
            ("r9", "v", "HOSPITAL.pdf", "2018-06-15", "", "Procedure", "Procedure", 1.0, "", "single", "auto"),
        ])
        c.commit()
    return health_db


def test_plan_blocks_billing_rows(db_promote):
    import lab_promote
    r = lab_promote.plan("r9", None, execute=False)
    assert r["blocked"] == 2      # DRUG_X(валюта) + Procedure(не-аналит)
    assert r["promote"] == 1      # только Glucose


def test_execute_keeps_only_real_analyte(db_promote):
    import lab_promote
    lab_promote.plan("r9", None, execute=True)
    with db_promote.get_conn() as c:
        names = {r["test_name"] for r in c.execute("SELECT test_name FROM lab_results")}
    assert "Glucose" in names
    assert "DRUG_X" not in names and "Procedure" not in names


def test_страница_график_не_промоутится(db):
    """Вычисленное отклонение на диаграмме не является измерением.
    Оба писателя должны применять один гейт роли страницы."""
    import lab_promote
    r = {"page_role": "derived_chart", "unit": "10^5 кл/г", "_specimen": "stool",
         "_cname": "Учебный показатель диаграммы", "value": -840.0}
    assert lab_promote._block_reason(r) == "page-role:derived_chart"


def test_страница_текст_не_промоутится(db):
    """Придуманный текстовый комментарий не становится именем аналита."""
    import lab_promote
    r = {"page_role": "narrative", "unit": "", "_specimen": "stool",
         "_cname": "Учебный комментарий к структуре таблицы", "value": None}
    assert lab_promote._block_reason(r) == "page-role:narrative"


def test_роль_неизвестна_строку_не_блокируем(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ. NULL = страницу никто не смотрел (не PDF или разбор
    до 31.07). Блокировать такие значило бы задним числом выкосить историю, о
    которой правило ничего не знает."""
    import lab_promote
    r = {"page_role": None, "unit": "mg/dL", "_specimen": "blood",
         "_cname": "Glucose", "value": 95.0, "_class": "chemistry", "_home": "canon"}
    assert lab_promote._block_reason(r) is None


# ── Гейт тенанта: документ обязан лежать внутри HEALTH_DATA_DIR (2026-08-08) ──
# Разрыв найден прогоном линз: тенант выбирается ПЕРЕМЕННОЙ ОКРУЖЕНИЯ, документ
# приезжает АБСОЛЮТНЫМ путём, сверки не было. Партнёрский бланк, разобранный из
# привычной сессии, лёг бы в канон владельца, а промоут «покрыл» бы им родные строки.

def test_document_outside_the_tenant_is_refused(tmp_path, monkeypatch):
    """ОТКАЗ, а не предупреждение: предупреждение перед разрушающей записью читают
    уже после неё. Краснеет от снятия _assert_belongs_to_tenant."""
    import lab_backfill
    mine, alien = tmp_path / "health_partner", tmp_path / "health"
    (mine / "incoming").mkdir(parents=True)
    (alien / "incoming").mkdir(parents=True)
    doc = alien / "incoming" / "owner.pdf"
    doc.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(mine))
    with pytest.raises(ValueError) as e:
        lab_backfill.resolve_document(str(doc))
    assert "вне тенанта" in str(e.value)
    assert "HEALTH_DATA_DIR" in str(e.value), "в отказе нет инструкции, как запускать верно"


def test_document_inside_the_tenant_passes(tmp_path, monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: свой документ обязан проходить, иначе гейт = отказ работать."""
    import lab_backfill
    mine = tmp_path / "health_partner"
    (mine / "incoming").mkdir(parents=True)
    doc = mine / "incoming" / "partner.pdf"
    doc.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(mine))
    assert lab_backfill.resolve_document(str(doc)) == doc


def test_search_by_name_cannot_cross_into_another_tenant(tmp_path, monkeypatch):
    """Поиск ПО ИМЕНИ опаснее абсолютного пути: одинаково названный бланк в чужом
    доме находится молча. Гейт обязан стоять и на этой ветке."""
    import lab_backfill
    mine, alien = tmp_path / "health_partner", tmp_path / "health"
    (mine / "incoming").mkdir(parents=True)
    # Именно ПОД CR: glob ищет в `inbox` и в `ICLOUD_ROOT/CR`, и первая редакция этого
    # теста клала файл мимо обеих веток — не раскрывалась, а просто не находила.
    (alien / "CR" / "sub").mkdir(parents=True)
    (alien / "CR" / "sub" / "labs.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(mine))
    monkeypatch.setattr(lab_backfill, "ICLOUD_ROOT", alien)
    with pytest.raises(ValueError):
        lab_backfill.resolve_document("labs.pdf")
