"""«Документа нет», когда он есть: карточка предложения показывает документ (нить treatment-tails).

Синтетика. 04.10.2026: GP дважды предложил в заметку проблемы «результат обследования
не зафиксирован в системе», и предложение одобрили, не видя, что документ за тот месяц
лежит в медкарте. Оракул: карточка называет документ; без утверждения об отсутствии — молчит.
Красный на коде до нити.
"""
import gp_context


def test_судья_находит_документ_по_слову_в_имени():
    text = "Дедлайн прошёл. DATA GAP: результат контрольного DEMO-скана апрель 2031 не зафиксирован в системе."
    docs = [("2031-04-26", "DOCS/DEMO 04.2031.pdf"), ("2031-01-05", "DOCS/Other visit.pdf")]
    assert gp_context.documents_answering_absence(text, docs) == [("2031-04-26", "DOCS/DEMO 04.2031.pdf")]


def test_без_утверждения_об_отсутствии_судья_молчит():
    docs = [("2031-04-26", "DOCS/DEMO 04.2031.pdf")]
    assert gp_context.documents_answering_absence("DEMO-скан апрель 2031: чисто.", docs) == []


def test_карточка_показывает_документ_до_решения(db):
    import proposals_db
    db.execute("INSERT INTO problem_list (problem_id, title, status, notes, first_seen, last_updated) "
               "VALUES ('P900', 'Состояние Д', 'watchful_waiting', 'старое', '2030-01-01', '2031-01-01')")
    db.execute("INSERT INTO events (event_type, effective_date, status, notes) "
               "VALUES ('encounter', '2031-04-26', 'completed', 'DOCS/DEMO 04.2031.pdf')")
    card = proposals_db.format_proposal_card({"id": 1, "source": "gp_weekly", "repeats": 1,
        "proposed": '[{"action": "update_field", "problem_id": "P900", "field": "notes", '
                    '"new_value": "DATA GAP: результат DEMO-скана не зафиксирован в системе"}]'})
    assert "DEMO 04.2031.pdf (2031-04-26)" in card, card


def test_без_подтверждения_в_документах_тоже_утверждение_об_отсутствии():
    """05.10.2026: так звучало предложение GP, одобренное при документе в медкарте."""
    text = "Дедлайн истёк без подтверждённого выполнения DEMO-скана в документах."
    docs = [("2031-04-26", "DOCS/DEMO 04.2031.pdf")]
    assert gp_context.documents_answering_absence(text, docs) == [("2031-04-26", "DOCS/DEMO 04.2031.pdf")]

