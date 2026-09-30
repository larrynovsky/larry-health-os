"""Census-датчик: каждый контакт кода с iCloud зарегистрирован в icloud_contacts.yaml.

Класс бага: iCloud — ленивая репликация без датчиков; контакт кода с ней,
не учтённый при миграциях, тихо деградирует (прецедент: monthly_consilium
месяц читал заархивированный каталог промптов через silent fallback).
Реестр = данные; новый писатель/читатель iCloud обязан объявить kind и
verdict, иначе этот тест падает.
"""
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]
_REGISTRY = _REPO / "icloud_contacts.yaml"

_MARKER = "Mobile Documents"
# С 2026-09-24 (BL-PUB-12) у облачного пути один дом — infra_config; контакт = литерал ИЛИ
# обращение к дому. Без второго признака census ослеп бы ровно на тот день, когда литералы ушли.
_HOME_USES = ("cloud_dir(", "CLOUD_HEALTH_DIR", "HAE_APP_DIR", "CONSTITUTIONS_MIRROR")
# Где литерал законен: дом и стражи, которые перечисляют облачные папки, чтобы ЗАПРЕТИТЬ их.
_LITERAL_ALLOWED = {"infra_config.py", "health_db.py", "integrity_tests.py"}
_SKIP_DIRS = ("tests/", "__pycache__", "_Archive", ".git")


def _registered() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return {c["file"] for c in data["contacts"]}


def _actual_contacts() -> set[str]:
    found = set()
    for py in _REPO.rglob("*.py"):
        rel = str(py.relative_to(_REPO))
        if any(s in rel for s in _SKIP_DIRS):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if _MARKER in text or any(u in text for u in _HOME_USES):
            found.add(rel)
    return found


def _literal_holders(root: Path = _REPO) -> set[str]:
    return {str(py.relative_to(root)) for py in root.rglob("*.py")
            if not any(s in str(py.relative_to(root)) for s in _SKIP_DIRS)
            and _MARKER in py.read_text(encoding="utf-8", errors="ignore")}


def test_cloud_literal_only_in_home():
    """Литерал облачного пути — только в доме (infra_config) и стражах. До 24.09 он жил в 27
    файлах; у постороннего каждый такой файл читал/писал в ЕГО iCloud без спроса (BL-PUB-12)."""
    stray = sorted(_literal_holders() - _LITERAL_ALLOWED)
    assert not stray, (f"Литерал облачного пути вне infra_config: {stray}. "
                       "Бери infra_config.cloud_dir(...) / CLOUD_HEALTH_DIR / HAE_APP_DIR.")


def test_literal_ratchet_sees_a_planted_literal(tmp_path):
    """Отрицательный контроль: подброшенный литерал ловится (иначе зелёный ничего не значит)."""
    (tmp_path / "planted.py").write_text('X = "~/Library/Mobile Documents/com~apple~CloudDocs"')
    (tmp_path / "infra_config.py").write_text('Y = "Library/Mobile Documents"')
    assert _literal_holders(tmp_path) - _LITERAL_ALLOWED == {"planted.py"}


def test_registry_file_exists():
    assert _REGISTRY.exists()


def test_every_icloud_contact_is_registered():
    actual = _actual_contacts()
    registered = _registered()
    unregistered = sorted(actual - registered)
    assert not unregistered, (
        f"Контакт с iCloud без записи в icloud_contacts.yaml: {unregistered}. "
        "Объяви kind (source_inbox/staging/derived_export/guard/journal/"
        "legacy_oneshot) и note — или не трогай iCloud из кода.")


def test_registry_has_no_dead_entries():
    """Симметрия census: запись без фактического контакта = мусор реестра."""
    actual = _actual_contacts()
    dead = sorted(_registered() - actual)
    assert not dead, (
        f"Записи реестра без контакта в коде (файл удалён/мигрирован?): {dead}")
