"""BL-DOCS-DOCKER-2 (01.10): посадочная docs/how-to/README.md делит инструкции на три читателя
(бот / обслуживание установки / разработка). Падение = в папке появилась инструкция, которой
нет ни в одном списке: читатель установки из образа её не найдёт и не узнает, к кому она."""
import re
from pathlib import Path

HOWTO = Path(__file__).resolve().parents[2] / "docs" / "how-to"


def test_каждая_открытая_инструкция_названа_на_посадочной():
    """Закрытые страницы (publication_zones.yaml) посадочная называть НЕ должна — ссылка в
    закрытую часть ломает открытый репозиторий (test_public_doc_refs)."""
    import pii_census as pc
    globs = pc._zones(pc.ROOT)
    landing = (HOWTO / "README.md").read_text()
    linked = set(re.findall(r"\]\(([\w.-]+\.md)\)", landing))
    pages = {p.name for p in HOWTO.glob("*.md")
             if p.name != "README.md" and not p.name.endswith(".en.md")
             and not pc._is_private(f"docs/how-to/{p.name}", globs)}
    assert pages - linked == set(), f"нет на посадочной: {sorted(pages - linked)}"
