"""Contract-тест дома формата квитанции (внешнее ревью 2026-08-07, F-02).

Класс, который закрываем: ссылка «формат — там-то» вела в документ, где схемы не было;
substring-сторож этого не видел, потому что проверял имя раздела, а не содержимое.
Здесь примеры нормативного дока ИСПОЛНЯЮТСЯ через живой судья: valid-примеры обязаны
проходить, invalid — краснеть заявленным правилом. Схема разошлась с судьёй → красный.
"""
import re
from pathlib import Path

from project_context import intentgate

DOC = Path(__file__).resolve().parents[2] / "docs" / "reference" / "intent_receipt_format.md"

# Реестр-фикстура, под который написаны примеры дока.
REG = {"alpha": {"a_holds": "holds", "a_open": "open"}, "beta": {"b1": "holds"}}

_EX_RE = re.compile(r"<!-- example: ([\w-]+) -->\s*```yaml\n(.*?)```", re.DOTALL)


def _examples():
    text = DOC.read_text(encoding="utf-8")
    found = dict(_EX_RE.findall(text))
    assert found, f"в {DOC} нет размеченных примеров <!-- example: ... -->"
    return found


def _judge(name, body, path="plans/PLAN_doc_example.md"):
    if name.startswith("review"):
        path = "docs/handoff/x/2026-08-07-abc1234.review.md"
    files = {path: f"# Пример\n\n## Замысел\n\n```yaml\n{body}```\n"}
    blocks, _ = intentgate.evaluate_files(files, REG)
    return blocks


def test_doc_exists_and_names_required_fields():
    text = DOC.read_text(encoding="utf-8")
    for marker in ("intent:", "invariants", "read_at", "R6", "## Замысел"):
        assert marker in text, f"нормативный док не называет {marker!r}"


def test_valid_examples_pass_live_judge():
    ex = _examples()
    for name in ("full-valid", "none-valid", "review-valid"):
        assert name in ex, f"в доке нет примера {name}"
        assert _judge(name, ex[name]) == [], f"valid-пример {name} не проходит живой судья"


def test_invalid_examples_redden_with_declared_rule():
    ex = _examples()
    for name, rule in (("full-subset-invalid", "R6"), ("full-empty-invalid", "R6")):
        assert name in ex, f"в доке нет примера {name}"
        blocks = _judge(name, ex[name])
        assert blocks and rule in blocks[0], (
            f"invalid-пример {name} обязан краснеть правилом {rule}, получено: {blocks}")


def test_references_point_to_this_home():
    """Ссылки судьи и CLI ведут в существующий дом, а не в скилл вне репозитория."""
    root = DOC.parents[2]
    for src in ("project_context/intentgate.py", "project_context/__main__.py"):
        text = (root / src).read_text(encoding="utf-8")
        assert "docs/reference/intent_receipt_format.md" in text, (
            f"{src} не ссылается на нормативный дом формата")
