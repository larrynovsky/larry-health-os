"""lab_reconcile._candidates исключает инвойсы (#67 Layer 1).

Инвойс/чек утекал в vision → мусор (у нескольких аналитов значение 1.0 = кол-во заказа; €=цена).
Фикс: _candidates отсекает биллинг-типы И финансовые-по-имени (is_financial),
ловя инвойсы в general_medical/пустом типе. Реальные лаб-отчёты в Payments/ не режет.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS imported_docs(source_file TEXT, doc_type TEXT)")
        c.executemany("INSERT INTO imported_docs(source_file,doc_type) VALUES(?,?)", [
            ("CR/lab eng.pdf", "lab"),                 # лаб → не кандидат (уже лаб)
            ("CR/HOSPITAL_BILL_a.pdf", "hospital_bill"),      # биллинг-тип → skip
            ("CR/Claim_form_01.pdf", "general_medical"),  # инвойс по имени → skip
            ("CR/invoice_drug.pdf", ""),                      # инвойс, пустой тип → skip
            ("CR/Payments /A/report_a.pdf", "general_medical"),  # лаб-отчёт → кандидат
            ("CR/discharge_note.pdf", "discharge"),             # не-лаб мед → кандидат
        ])
        c.commit()
    return health_db


def test_candidates_excludes_invoices_keeps_real_labs(db):
    import lab_reconcile
    cands = set(lab_reconcile._candidates())
    # инвойсы отсеяны (биллинг-тип + по имени в general_medical/пустом типе)
    assert "CR/HOSPITAL_BILL_a.pdf" not in cands
    assert "CR/Claim_form_01.pdf" not in cands
    assert "CR/invoice_drug.pdf" not in cands
    # уже-лаб не сканируется повторно
    assert "CR/lab eng.pdf" not in cands
    # реальный лаб-отчёт в Payments/ по имени НЕ финансовый → остаётся кандидатом
    assert "CR/Payments /A/report_a.pdf" in cands
    # прочий не-лаб мед документ — кандидат на скан
    assert "CR/discharge_note.pdf" in cands
