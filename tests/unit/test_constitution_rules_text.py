"""Правила конституций в промпте — без навигации по языкам (29.09, docs-en-root)."""


def test_prompt_rules_carry_no_language_switch():
    import generate_constitutions as gc
    text = gc._rules_text()
    assert "[English](" not in text and text.startswith("# ")
