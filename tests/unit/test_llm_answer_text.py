"""Ответ модели читается одним читателем — llm_client.answer_text (инцидент 03.10).

claude-sonnet-5-5 кладёт первым блок рассуждения; код с `content[0].text` упал в
утреннем отчёте, ночном разборе и консолидации памяти, а допуск модели, читавший
текстовые блоки, её пропустил. Оракулы:
  • ответ «рассуждение, затем текст» отдаёт текст, а старый способ чтения на нём падает;
  • в рабочем коде нет ни одного `.content[0].text` (храповик: новый вызов краснеет);
  • допуск и рабочий код читают одной функцией.
"""
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import llm_client

ROOT = Path(__file__).resolve().parents[2]


def _resp(*blocks, stop="end_turn"):
    return NS(content=list(blocks), stop_reason=stop)


THINK = NS(type="thinking", thinking="…", signature="x")


def test_thinking_first_gives_text_and_old_reader_breaks():
    r = _resp(THINK, NS(type="text", text="Доброе утро"))
    assert llm_client.answer_text(r) == "Доброе утро"
    with pytest.raises(AttributeError):
        r.content[0].text


def test_no_text_is_loud_with_stop_reason():
    with pytest.raises(llm_client.NoTextInAnswer, match="max_tokens"):
        llm_client.answer_text(_resp(THINK, stop="max_tokens"))


def test_tool_use_is_not_text_and_texts_join():
    r = _resp(NS(type="text", text="a"), NS(type="tool_use", name="t", input={}),
              NS(type="text", text="b"))
    assert llm_client.answer_text(r) == "ab"


def test_no_content0_text_readers_in_working_code():
    import git_facts   # работает и в песочнице без .git (манифест) — §20: зелёный не от среды
    files = [f for f in git_facts.tracked("*.py")
             if not f.startswith(("tests/", "logs/")) and f != "llm_client.py"]
    assert len(files) > 100, "сторож ослеп: файлов кода не нашлось"
    hits = [f"{f}:{i}" for f in files
            for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\.content\[0\]\.text", line)]
    assert not hits, "читать ответ модели только через llm_client.answer_text: " + ", ".join(hits)


def test_admission_reads_with_the_same_reader():
    src = (ROOT / "llm_admission.py").read_text(encoding="utf-8")
    body = src.split("def _call(", 1)[1].split("\ndef ", 1)[0]
    assert "llm_client.answer_text(" in body
    assert not re.search(r'getattr\(b, "type"', body)
