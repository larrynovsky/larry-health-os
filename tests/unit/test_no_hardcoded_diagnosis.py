"""Позит-контроль обоих направлений для diagnosis_guard (нить diagnosis-hardcode).

RST: check≠test. Страж без доказанного «ловит рецидив» — фейк-зелёный. Здесь:
  1) чистое (починенное) дерево → PASS (негативное направление);
  2) подсаженный литерал → пойман (позитивное направление);
  3) .py-комментарий (tombstone) НЕ даёт ложный хит;
  4) пропавший сайт-файл → нарушение (liveness, §14), не тихо-зелёное.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import diagnosis_guard as dg

REPO = Path(__file__).resolve().parents[2]


def test_clean_tree_passes():
    hits = dg.scan(REPO)
    assert hits == [], f"страж красит чистое дерево (ложный хит): {hits}"


def test_injected_literal_is_caught(tmp_path):
    for rel in dg.SITES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# чисто\nобычный текст без диагноза\n", encoding="utf-8")
    (tmp_path / "specialists" / "lifestyle_sleep.md").write_text(
        "Если онколог предлагает — не молчи\n", encoding="utf-8")
    hits = dg.scan(tmp_path)
    assert any("lifestyle_sleep.md" in h and "онколог" in h for h in hits), hits


def test_python_comment_is_ignored(tmp_path):
    for rel in dg.SITES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("чисто\n", encoding="utf-8")
    (tmp_path / "hai_core.py").write_text(
        "# tombstone: печатал Онкологический статус безусловно\n"
        "x = 1\n", encoding="utf-8")
    hits = dg.scan(tmp_path)
    assert not any("hai_core.py" in h for h in hits), f"комментарий дал ложный хит: {hits}"


def test_missing_site_file_flagged(tmp_path):
    hits = dg.scan(tmp_path)
    assert any("ФАЙЛ ОТСУТСТВУЕТ" in h for h in hits), hits


def test_guard_diplotypes_live_in_private_dictionary():
    """Решение владельца 30.09 «генотип прятать»: сторож не держит в себе литералы диплотипов —
    иначе публикует то, что стережёт. Берёт их из приватного словаря (класс genotype)."""
    import pathlib
    import pytest
    import diagnosis_guard
    import pii_census
    lits = pii_census.literals(["genotype"])
    if not lits:
        pytest.skip("приватного словаря нет (публичная установка) — стережётся только имя константы")
    src = pathlib.Path(diagnosis_guard.__file__).read_text(encoding="utf-8")
    assert not [t for t in lits if t in src], "диплотип зашит в сторож"
    assert {t.lower() for t in lits} <= set(diagnosis_guard.SITES["dashboard_routers/views.py"])
