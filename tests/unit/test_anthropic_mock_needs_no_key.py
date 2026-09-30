"""Заглушка модели не должна зависеть от файла ключа на машине (§20).

Репетиция экспорта 2026-09-24: 12 тестов с anthropic_mock падали в чистой копии
(нет ~/.health_secrets/anthropic_key) и зеленели с ПОДДЕЛЬНЫМ ключом — то есть их
зелёный на Studio держался на настоящем файле владельца, а не на тесте.
"""
from pathlib import Path


def test_client_is_built_without_key_file(anthropic_mock, monkeypatch, tmp_path):
    import hai_core
    import llm_client
    monkeypatch.setattr(llm_client, "_KEY_FILE", tmp_path / "нет_такого_файла")
    anthropic_mock.script(match=lambda p: True, response="ok")
    client = hai_core.get_client()
    resp = client.messages.create(model="m", max_tokens=5,
                                  messages=[{"role": "user", "content": "проба"}])
    assert resp.content[0].text == "ok"
