"""Служебный канал: notify_operator шлёт ВЛАДЕЛЬЦУ из процесса ТЕНАНТА.

Регрессия 2026-08-01: вотчер партнёра звал `notify.notify`, тот берёт получателя
из `secrets_dir()` (честно-per-tenant), и трое суток запросы на ревью уходили
самому партнёру — 19278 сообщений. Решение владельца: служебное — оператору,
содержательное — тому, о ком оно.

Оракул проверяет ПОЛУЧАТЕЛЯ, а не факт вызова: подменяем `_telegram` на шпиона
и смотрим, из какого каталога взяты секреты.
"""
import pytest


@pytest.fixture
def tenant_env(tmp_path, monkeypatch):
    """Процесс тенанта: HEALTH_DATA_DIR/HEALTH_SECRETS_DIR указывают на партнёра."""
    (tmp_path / "secrets_partner").mkdir()
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path / "secrets_partner"))
    monkeypatch.setenv("HOME", str(tmp_path))          # владелец = tmp/.health_secrets
    return tmp_path


def _spy(monkeypatch, mod):
    seen = []
    monkeypatch.setattr(mod, "_telegram", lambda msg, secrets=None: (seen.append(secrets), True)[1])
    return seen


def test_operator_channel_goes_to_owner_from_tenant_process(tenant_env, monkeypatch):
    """ПОЗИТИВ: служебное из процесса тенанта уходит владельцу."""
    import importlib
    import notify as n
    n = importlib.reload(n)
    seen = _spy(monkeypatch, n)
    assert n.notify_operator("служебное") == "telegram"
    assert seen[0] is not None, "служебное ушло по тенантскому каналу"
    assert seen[0].name == ".health_secrets", seen[0]


def test_content_channel_stays_with_the_tenant(tenant_env, monkeypatch):
    """НЕГАТИВ (не пересолили): содержательное по-прежнему уходит тенанту."""
    import importlib
    import notify as n
    n = importlib.reload(n)
    seen = _spy(monkeypatch, n)
    assert n.notify("содержательное") == "telegram"
    assert seen[0] is None, "содержательное увели у тенанта"


def test_watcher_keeps_operator_and_person_channels():
    """02.10: содержательный результат — человеку; служебный путь сохранён отдельно.
    Получателя и отсутствие повторов проверяет test_auto_rows_reach_own_chat_and_stay_out_of_canon.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "lab_intake_watcher.py"
    text = src.read_text(encoding="utf-8")
    # 02.10 (нить llm-tails): +1 служебный — «чтение анализов не допущено у поставщика установки»,
    # +1 сбой — «человек не получил это сообщение».
    # 05.10 (нить lab-intake-retry): +1 служебный — «ключ или баланс поставщика пуст» у тенанта
    # не-владельца: ключ установки чинит оператор, человек тенанта этого сделать не может.
    assert text.count("notify.notify_operator(") == 3
    assert "notify.notify(" in text and "fallback=False" in text


def test_conftest_blocks_real_delivery():
    """Позитивный контроль глушителя: без монкипатча доставка физически отрезана.

    Краснеет, если autouse-фикстуру `_no_real_telegram` снимут или она перестанет
    накрывать `notify`. Именно её отсутствие 01.08 отправило четыре тестовых
    сообщения владельцу в настоящий Telegram.
    """
    import pytest as _pytest
    import notify as n
    with _pytest.raises(AssertionError, match="НАСТОЯЩЕЕ сообщение"):
        n._telegram("проверка глушителя")
    with _pytest.raises(AssertionError, match="НАСТОЯЩЕЕ сообщение"):
        n._healthcheck_fail("проверка глушителя")
