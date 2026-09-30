"""Датчики: унификация имени CA19-9 + page-cap распознавателя (анти-runaway)."""
from pathlib import Path

import pytest

import lab_canon


def test_ca19_9_normalizes_to_canonical():
    assert lab_canon.normalize("CA19_9") == "CA19-9"
    assert lab_canon.normalize("CA19-9") == "CA19-9"
    assert lab_canon.normalize("ca 19-9") == "CA19-9"
    assert lab_canon.normalize("CA19.9") == "CA19-9"   # имя из signal_family валид-гейта


def test_import_all_maps_ca19_to_canonical():
    src = (Path(__file__).resolve().parents[2] / "import_all.py").read_text(encoding="utf-8")
    assert '"Ca 19": "CA19-9"' in src        # источник раскола сведён к канону
    assert '"Ca 19": "CA19_9"' not in src    # старое имя-цель убрано


def test_render_pages_caps_at_max_pages(tmp_path):
    fitz = pytest.importorskip("fitz")
    import lab_recognizer
    p = tmp_path / "big.pdf"
    doc = fitz.open()
    for _ in range(lab_recognizer._MAX_PAGES + 5):
        doc.new_page()
    doc.save(str(p))
    doc.close()
    pages = lab_recognizer._render_pages(p)
    assert len(pages) == lab_recognizer._MAX_PAGES


# Адресное перечитывание позволяет выбрать страницу за общей отсечкой.
# Количество выбранных страниц ограничено, номер страницы сам по себе — нет.

def _pdf(tmp_path, n: int):
    fitz = pytest.importorskip("fitz")
    p = tmp_path / f"doc{n}.pdf"
    doc = fitz.open()
    for i in range(n):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"PAGE-{i + 1}", fontsize=40)
    doc.save(str(p))
    doc.close()
    return p


def test_explicit_pages_render_only_those(tmp_path):
    """Позитив: просим две страницы — получаем ровно две."""
    import lab_recognizer
    assert len(lab_recognizer._render_pages(_pdf(tmp_path, 30), pages=[6, 17])) == 2


def test_explicit_pages_bypass_the_index_cap(tmp_path):
    """Осознанно выбранная страница за отсечкой остаётся доступной."""
    import lab_recognizer
    n = lab_recognizer._MAX_PAGES + 30
    assert len(lab_recognizer._render_pages(_pdf(tmp_path, n), pages=[n])) == 1


def test_too_many_explicit_pages_is_refused_loudly(tmp_path):
    """Предел КОЛИЧЕСТВА остаётся: осознанный выбор — не отмена анти-runaway.
    Отказ громкий, а не тихое усечение: усечение выглядело бы как полный прогон."""
    import lab_recognizer
    with pytest.raises(ValueError):
        lab_recognizer._render_pages(_pdf(tmp_path, 3),
                                     pages=list(range(1, lab_recognizer._MAX_PAGES + 2)))


def test_page_number_in_provenance_is_the_real_one(tmp_path, monkeypatch):
    """ПРОВЕНАНС НЕ ВРЁТ. `enumerate(start=1)` приписал бы строкам со стр. 6 номер 1 —
    и человек, ищущий строку в бланке глазами, открыл бы не ту страницу. Оракул этот
    краснеет от возврата к enumerate."""
    import lab_recognizer as lr
    monkeypatch.setattr(lr, "_render_pages", lambda p, pages=None: [b"A", b"B"])
    monkeypatch.setattr(lr, "_vision_call", lambda img, prompt, model: [
        {"canonical_name": "Glucose", "raw_name": "Glucose", "value": 5.0, "unit": "mg/dL"}])
    res = lr.recognize(tmp_path / "x.pdf", "2032-04-12", pages=[6, 17])
    assert sorted({t["page"] for t in res["tests"]}) == [6, 17]
    assert res["stats"]["page_numbers"] == [6, 17]
