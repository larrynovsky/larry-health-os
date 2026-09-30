"""Английские переводы документации верны оригиналу по форме (28.09, нить i18n).

Решение владельца 28.09: документация — в двух вариантах, русском и английском. Перевод лежит
рядом с оригиналом (`<имя>.en.md`) и первой строкой называет источник и его хеш:
    <!-- translation-of: docs/how-to/x.md sha256:<12 hex> -->
Переводит в том числе Codex, поэтому форма судится машиной, а не глазами:
- команды и код (огороженные блоки) — байт в байт, иначе читатель получит другую команду;
- заголовки — то же число, иначе потерян раздел;
- цели ссылок — те же;
- оригинал ссылается на перевод (вход читателя, иначе перевод никто не найдёт).
Правила живут в doc_translation.py (один дом для теста, doc_agent и ночного датчика).
Устаревание (оригинал поменялся после перевода) здесь не красное: переводы догоняют позже,
список устаревших печатает doc_translation.stale_translations().
"""
import pytest

import doc_translation as dt
from doc_translation import ROOT, form_problems, stale_translations  # noqa: F401  (stale — вход датчика)

pytestmark = pytest.mark.unit


def translations(root=ROOT):
    return dt.translations(root)


def parse(en_path):
    return dt.mark_of(en_path.read_text(encoding="utf-8"))


def test_every_translation_names_existing_source():
    bad = []
    for en in translations():
        src, _ = parse(en)
        if not src:
            bad.append(f"{en.relative_to(ROOT)}: нет строки translation-of")
        elif not (ROOT / src).exists():
            bad.append(f"{en.relative_to(ROOT)}: источник {src} не найден")
    assert not bad, "\n".join(bad)


def test_translation_form_matches_source():
    bad = []
    for en in translations():
        src, _ = parse(en)
        if not src or not (ROOT / src).exists():
            continue
        probs = dt.pair_problems((ROOT / src).read_text(encoding="utf-8"), en.read_text(encoding="utf-8"))
        bad += [f"{en.relative_to(ROOT)}: {p}" for p in probs]
    assert not bad, "\n".join(bad)


def test_source_links_to_its_translation():
    bad = []
    for en in translations():
        src, _ = parse(en)
        if src and (ROOT / src).exists():
            if f"]({en.name})" not in (ROOT / src).read_text(encoding="utf-8"):
                bad.append(f"{src}: нет ссылки на {en.name}")
    assert not bad, "\n".join(bad)


def test_detector_sees_broken_translation():
    """Негативный контроль: изменённая команда, потерянный раздел, чужая ссылка — пойманы."""
    ru = "# A\n\n## B\n\n```bash\nls -la\n```\n\n[x](y.md)\n"
    assert form_problems(ru, ru) == []
    assert form_problems(ru, ru.replace("ls -la", "ls")) != []
    assert form_problems(ru, ru.replace("## B\n", "")) != []
    assert form_problems(ru, ru.replace("(y.md)", "(z.md)")) != []


def test_uc_readers_strip_language_switch():
    """Exercise the actual reader functions without importing application side effects."""
    import ast
    import re
    from pathlib import Path
    from unittest.mock import MagicMock, Mock

    raw = (dt.switch_line("ru", "USE_CASES.md", "USE_CASES.en.md") + "\n\n"
           "| `UC-Z-99` | Invented example | P0 | check | implemented | confirmed |\n")
    document = Mock()
    document.exists.return_value = True
    document.read_text.return_value = raw
    root = MagicMock()
    root.__truediv__.return_value = document
    cases = (
        ("generate_test.py", "parse_uc_catalog", (document,),
         {"UC-Z-99": dict(pragmatic="Invented example", priority="P0", type="check",
                         status="implemented", confirmation="confirmed")}),
        ("test_failure_handler.py", "_uc_status_for_test", ("test_uc_z_99_example",),
         "implemented"),
        ("validate_uc_index.py", "_confirmed_ucs_from_use_cases", (), {"UC-Z-99"}),
        ("tests/consistency/test_doc_reader_path.py", "_read", ("USE_CASES.md",),
         dt.strip_switch(raw)),
    )
    for filename, name, args, expected in cases:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        # Keep the reader and its real table regex; exclude imports that load production state.
        nodes = [node for node in tree.body
                 if (isinstance(node, ast.FunctionDef) and node.name == name)
                 or (isinstance(node, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id == "_UC_TABLE_ROW" for t in node.targets))]
        strip = Mock(wraps=dt.strip_switch)
        namespace = dict(Path=Path, re=re, strip_switch=strip, SCRIPT_DIR=root,
                         USE_CASES=document, ROOT=root)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)
        assert namespace[name](*args) == expected, filename
        strip.assert_called_once_with(raw)
        if name == "_read":
            strip.reset_mock()
            assert namespace[name]("docs/example.md") == raw
            strip.assert_not_called()


def test_stale_detector_and_its_nightly_delivery(tmp_path, monkeypatch):
    """28.09.2026: stale_translations() считал отставшие переводы, но его никто не звал.
    Положительный контроль детектора на выдуманном дереве + доставка в ночной integrity."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    fresh = dt.text_hash("# A\n")
    (tmp_path / "docs" / "a.en.md").write_text(
        f"<!-- translation-of: docs/a.md sha256:{fresh} -->\n# A\n", encoding="utf-8")
    assert dt.stale_translations(tmp_path) == []
    (tmp_path / "docs" / "a.md").write_text("# A changed\n", encoding="utf-8")
    assert dt.stale_translations(tmp_path) == ["docs/a.en.md"]

    import integrity_tests as it
    monkeypatch.setattr(dt, "stale_translations", lambda: ["docs/a.en.md"])
    before = len(it._warnings)
    assert it.check_translations_fresh() == {"stale": 1}
    assert any("docs/a.en.md" in w[1] for w in it._warnings[before:])
