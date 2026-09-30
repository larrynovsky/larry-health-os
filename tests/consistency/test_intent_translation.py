"""Английские тёплые страницы: свежие, честные по статусу, с разделом про пределы (28.09).

Перевод делает модель (doc_agent --translate-intent), поэтому честность судится машиной
тем же сторожем, что и русская страница, но по английскому словарю warm_guard.absent_en.
Без английского словаря сторож на переводе молчал бы всегда — русских фраз там нет.
Граница как у русского сторожа: ловятся только перечисленные формы, полнота — за ревьюером.
"""
import re

import pytest

import doc_agent as da
import doc_translation as dt
import intent_registry as ir

pytestmark = pytest.mark.unit

ROOT = dt.ROOT
_ENTRIES = ir.load_registry()
_CYR = re.compile(r"[А-Яа-яЁё]")


def _en_path(e):
    ru = ROOT / e["explanation"]
    return ru, ru.with_name(ru.stem + ".en.md")


def test_every_warm_guard_has_english_phrases():
    bad = []
    for e in _ENTRIES:
        for inv in e.get("invariants", []):
            wg = inv.get("warm_guard") or {}
            if wg.get("absent") and not wg.get("absent_en"):
                bad.append(f"{inv['id']}: нет absent_en")
            bad += [f"{inv['id']}: кириллица в «{p}»" for p in wg.get("absent_en", []) if _CYR.search(p)]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("entry_id", [e["id"] for e in _ENTRIES])
def test_english_page_fresh_and_honest(entry_id):
    """ЖЁСТКИЙ: у тёплой страницы есть перевод от ТЕКУЩЕЙ русской версии, без отмывания
    статуса и с разделом про пределы. Лечение: doc_agent.py --translate-intent <id>."""
    e = ir.get_entry(entry_id)
    ru, en = _en_path(e)
    assert en.exists(), f"[{entry_id}] нет {en.name}: doc_agent.py --translate-intent {entry_id}"
    en_text = en.read_text(encoding="utf-8")
    _src, h = dt.mark_of(en_text)
    assert h == dt.text_hash(ru.read_text(encoding="utf-8")), (
        f"[{entry_id}] перевод от старой русской версии: doc_agent.py --translate-intent {entry_id}")
    hits = ir.status_laundering(e, en_text, lang="en")
    assert not hits, f"[{entry_id}] отмывание статуса в переводе: {hits}"
    if ir.non_holds_invariants(e):
        assert "limit" in en_text.lower(), f"[{entry_id}] в переводе нет раздела про пределы"


def test_detector_sees_bad_translation():
    """Негативный контроль судьи перевода: русское слово, отмывание, пропавшие пределы."""
    e = {"id": "x", "invariants": [{"id": "inv1", "claim": "c", "status": "open",
                                    "warm_guard": {"absent": ["работает всегда"],
                                                   "absent_en": ["always works"]}}]}
    ru = "# A\n\n## Пределы\n\nТекст `код_тест`.\n"
    good = "# A\n\n## Limits\n\nText `код_тест`.\n"
    assert da._en_problems(e, ru, good) == []
    assert any("русские" in p for p in da._en_problems(e, ru, good.replace("Text", "Текст")))
    assert any("отмывание" in p for p in da._en_problems(e, ru, good.replace("Text", "It always works")))
    assert any("пределы" in p for p in da._en_problems(e, ru, good.replace("Limits", "Scope")))
    assert any("заголовков" in p for p in da._en_problems(e, ru, good.replace("## Limits\n", "")))
    assert ir.status_laundering(e, "It always works.", lang="ru") == []


def test_ru_switch_inserted_once_before_title():
    page = "<!-- p -->\n\n# Title\n\nbody\n"
    once = da._ensure_ru_switch(page, "a.md", "a.en.md")
    assert once.index("](a.en.md)") < once.index("# Title")
    assert da._ensure_ru_switch(once, "a.md", "a.en.md") == once
    assert da._ru_body(once).startswith("# Title")
