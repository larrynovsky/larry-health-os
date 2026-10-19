"""Справка «что уходит в отчёт» не расходится со схемой: каждое поле, категория и подключение
из bug_report названо на странице (обе языковые версии). Новое поле без строки в справке — красный:
человек должен видеть состав до нажатия «Отправить»."""
from pathlib import Path

import pytest

import bug_report

ROOT = Path(bug_report.__file__).resolve().parent
FIELDS = ("schema", "report_id", "release", "platform", "category", "connected", "faults", "screen")


@pytest.mark.parametrize("page", ["docs/reference/bug_report_fields.md", "docs/reference/bug_report_fields.en.md"])
def test_reference_names_every_schema_value(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    rep = bug_report.build("other", report_id="0123456789abcdef:1", release="local",
                           platform="linux/x86_64", connected={}, faults=[], screen=[])
    assert set(rep) == set(FIELDS), "схема изменилась — обнови FIELDS и справку"
    for name in (*FIELDS, *bug_report.CATEGORIES, *rep["connected"]):
        assert f"`{name}`" in text, f"{page}: не названо `{name}`"
