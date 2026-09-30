"""Свежесть перевода судится по рукописной части (arch-en-gen, 29.09): тела GEN/AUTOGEN и
машинная строка версии не старят перевод; рукописная правка старит; незакрытый маркер не прячет."""
import doc_translation as d

DOC = """# Карта

**Версия:** 1.1 | **Дата:** 2000-01-01

Рукописный абзац.

<!-- GEN:GRAPH:START -->
```
a.py → b.py
```
<!-- GEN:GRAPH:END -->

<!-- BEGIN AUTOGEN: sensors (gen_testing_contracts.py) -->
| датчик | что |
<!-- END AUTOGEN: sensors -->
Хвост.
"""


def test_generated_bodies_and_machine_line_do_not_age_a_translation():
    h = d.text_hash(DOC)
    assert d.text_hash(DOC.replace("a.py → b.py", "a.py → c.py")) == h
    assert d.text_hash(DOC.replace("| датчик | что |", "| датчик | что | ещё |")) == h
    assert d.text_hash(DOC.replace("1.1 | **Дата:** 2000-01-01", "1.2 | **Дата:** 2000-01-02")) == h


def test_handwritten_change_ages_it():
    assert d.text_hash(DOC.replace("Рукописный абзац.", "Другой абзац.")) != d.text_hash(DOC)
    assert d.text_hash(DOC.replace("Хвост.", "Хвост!")) != d.text_hash(DOC)


def test_unclosed_marker_hides_nothing():
    broken = DOC.replace("<!-- GEN:GRAPH:END -->", "")
    assert d.text_hash(broken.replace("a.py → b.py", "a.py → c.py")) != d.text_hash(broken)


def test_form_ignores_generated_bodies_but_not_handwritten_fences():
    en_body = DOC.replace("```\na.py → b.py\n```", "```\nmodule graph in English\n```")
    en = "<!-- translation-of: x.md sha256:000000000000 -->\n" + en_body
    assert d.pair_problems(DOC, en) == []
    en_bad = en.replace("Рукописный абзац.", "```\nls\n```")
    assert d.pair_problems(DOC, en_bad), "рукописный огороженный блок по-прежнему сверяется"


def test_fence_prose_lines_translate_commands_do_not():
    ru = "# A\n\n```\n│ Роль: писатель │\nssh studio 'ls'\n```\n"
    ok = ru.replace("│ Роль: писатель │", "│ Role: the writer │")
    assert d.form_problems(ru, ok) == []
    assert d.form_problems(ru, ok.replace("ssh studio 'ls'", "ssh studio 'ls -la'")), "команда не переводится"
    assert d.form_problems(ru, ok.replace("│ Role: the writer │\n", "")), "строк в блоке столько же"


def test_version_line_is_machine_only_in_generated_documents():
    plain = "# Doc\n\n**Версия:** 0.1 | **Дата:** 2026-05-08\n\nТекст.\n"
    assert d.text_hash(plain.replace("0.1", "0.2")) != d.text_hash(plain), "рукописная версия судится"


def test_every_existing_pair_without_blocks_keeps_its_hash():
    import hashlib
    for en in d.translations():
        src, _ = d.mark_of(en.read_text(encoding="utf-8"))
        if not src:
            continue
        ru = (d.ROOT / src).read_text(encoding="utf-8")
        if "<!-- GEN:" in ru or "BEGIN AUTOGEN" in ru:
            continue
        assert d.text_hash(ru) == hashlib.sha256(ru.encode("utf-8")).hexdigest()[:12], src
