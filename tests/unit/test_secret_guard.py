"""Тесты secret_guard (SEC-21, BL-SECRETS-LLM-1).

Инварианты: значение секрета никогда не попадает в результат; словарные ключи
и короткие id не дают ложняков; слепой гард кричит, а не молчит; doc_agent
блокирует API-вызов до чтения api_key.
"""

from pathlib import Path

import pytest

import secret_guard as sg


FAKE_TOKEN = "sk-fake-9f8e7d6c5b4a3210aabbccdd"  # цифры + длина ≥12


def _secrets(tmp_path, **files) -> list[Path]:
    d = tmp_path / ".health_secrets"
    d.mkdir()
    for name, content in files.items():
        (d / name).write_text(content)
    return [d]


def test_planted_value_detected_name_only(tmp_path):
    dirs = _secrets(tmp_path, anthropic_key=FAKE_TOKEN + "\n")
    hits = sg.find_secret_values(f"KEY = '{FAKE_TOKEN}'", dirs=dirs)
    assert hits == [".health_secrets/anthropic_key"]
    assert FAKE_TOKEN not in " ".join(hits)  # трипвайр: значение не возвращается


def test_value_inside_json_detected(tmp_path):
    dirs = _secrets(tmp_path, token_json='{"access_token": "ya29.a0AbCd1234efGh5678"}')
    hits = sg.find_secret_values("diff: +TOKEN=ya29.a0AbCd1234efGh5678", dirs=dirs)
    assert hits == [".health_secrets/token_json"]


def test_dictionary_keys_and_short_ids_no_false_positives(tmp_path):
    dirs = _secrets(
        tmp_path,
        token_json='{"refresh_token": "abc123def456ghi789jkl"}',
        telegram_chat_id="1234567890\n",  # 10 цифр — ниже порога, принято
    )
    clean = "обсуждаем refresh_token и chat_id 1234567890 в коде"
    assert sg.find_secret_values(clean, dirs=dirs) == []


def test_clean_text_clean(tmp_path):
    dirs = _secrets(tmp_path, oura_token="OURA9876543210FEDCBA\n")
    assert sg.find_secret_values("обычный дифф без секретов", dirs=dirs) == []


def test_missing_dir_fails_closed(tmp_path):
    hits = sg.find_secret_values("любой текст", dirs=[tmp_path / "нет_такого"])
    assert len(hits) == 1 and hits[0].startswith("!secret_guard не отработал")


def test_doc_agent_blocks_before_api(monkeypatch, tmp_path):
    """Гард стоит ДО чтения api_key: блок не требует ни ключа, ни anthropic."""
    # import doc_agent тянет health_db; вне Studio нужен локальный HEALTH_DATA_DIR
    # (R1/R2-гард, feedback_runchecks_studio_only)
    hd = tmp_path / "hd"
    (hd / "data").mkdir(parents=True)
    sd = tmp_path / "secrets"
    sd.mkdir()
    monkeypatch.setenv("HEALTH_DATA_DIR", str(hd))
    # тенант-гард secrets_paths: нестандартный DATA_DIR требует явного SECRETS_DIR
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sd))
    import doc_agent
    sent = []
    monkeypatch.setattr(doc_agent.notifications, "notify_operator", lambda t: sent.append(t))
    # _log тоже мокаем: иначе тест пишет «SEC-21 BLOCK: anthropic_key» в БОЕВОЙ
    # doc_agent.log — ложная тревога будущему читателю (поймано 2026-07-06)
    monkeypatch.setattr(doc_agent, "_log", lambda m: None)
    monkeypatch.setattr(sg, "find_secret_values", lambda text, dirs=None: [".health_secrets/anthropic_key"])
    res = doc_agent.analyze_diff("msg", "diff с секретом")
    assert "SEC-21" in res["error"] and "заблокирована" in res["error"]
    assert len(sent) == 1 and "Ключ нужно заменить" in sent[0]
    assert "Варианты:" in sent[0] and "Если промолчишь" in sent[0]
    assert "SEC-21" not in sent[0] and "anthropic_key" not in sent[0]


def test_guard_failure_only_journals(monkeypatch, fault_journal):
    import doc_agent
    sent = []
    monkeypatch.setattr(doc_agent, "_log", lambda msg: None)
    monkeypatch.setattr(sg, "find_secret_values", lambda *a, **k: ["!secret_guard unavailable"])
    monkeypatch.setattr(doc_agent.notifications, "notify_operator", lambda text: sent.append(text))
    result = doc_agent.analyze_diff("fictional change", "fictional diff")
    assert "error" in result and sent == []
    assert "SEC-21" in fault_journal.read_text()
