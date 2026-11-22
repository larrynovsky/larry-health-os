"""Обрезанный по max_tokens перевод не пишется (16.11, ночной ремонт «переводы устарели»).

Замер: docs/reference/duplicate_catch_log.en.md от 08.10 (c87c05cc) оборван на полуслове —
модель упёрлась в max_tokens=8000, форма (код, заголовки, ссылки) хвоста таблицы не видит,
и translate_page проштамповал обрубок хешем полного оригинала: датчик свежести молчал 5 недель,
а ~35 строк журнала в английской версии не было. Значения выдуманы."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

import doc_agent
import llm_client


def _client(stop_reason: str):
    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(stop_reason=stop_reason,
                                   content=[SimpleNamespace(type="text", text="# A\n\nrow one\n")])
    return SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: _S()))


@pytest.fixture
def page(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\nстрока один\n\nстрока два\n", encoding="utf-8")
    monkeypatch.setattr(doc_agent, "ROOT", tmp_path)
    monkeypatch.setattr(doc_agent, "_read_secret", lambda name: "x")
    monkeypatch.setattr(doc_agent, "_log", lambda *a, **k: None)
    import secret_guard  # §20: настоящий каталог секретов тест не читает
    monkeypatch.setattr(secret_guard, "find_secret_values", lambda text: [])
    return tmp_path


def test_truncated_translation_is_not_written(page, monkeypatch):
    monkeypatch.setattr(llm_client, "guarded_client", lambda *a, **k: _client("max_tokens"))
    assert doc_agent.translate_page("docs/a.md") is False
    assert not (page / "docs" / "a.en.md").exists()


def test_complete_translation_is_written(page, monkeypatch):
    """Позитивный контроль: тот же ответ с end_turn пишется — отказ выше причинён обрезкой."""
    monkeypatch.setattr(llm_client, "guarded_client", lambda *a, **k: _client("end_turn"))
    assert doc_agent.translate_page("docs/a.md") is True
    assert (page / "docs" / "a.en.md").exists()
