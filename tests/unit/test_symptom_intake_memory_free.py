"""tests/unit/test_symptom_intake_memory_free.py — самополицейский гард ALLOWLIST-исключения.

symptom_intake внесён в ALLOWLIST контракта patient_context как memory-free ПО ЗАМЫСЛУ:
knowledge-base детекция опасного сознательно отвергнута автором (см.
docs/explanation/symptom_intake_hypothesis.md, §«Почему безопасность — это НЕ детекция
опасного»). ALLOWLIST выключает греп-детекцию контракта для этого файла — эти тесты
возвращают enforcement на две задокументированные инварианты:
  1) движок остаётся без памяти пациента (иначе заякорит анти-якорный мотор);
  2) несущая стена resolution_type='needs_specialist' (та же, что doctor_in_loop) держит:
     и сходимость, и тупик уходят живому врачу, движок сам вывод не эмитит.

Предел (честный): лок ловит доступ к памяти через САНКЦИОНИРОВАННЫЕ провайдеры — как и сам
контракт; произвольный прямой запрос к health_db он бы не увидел (тот же слепой угол).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

# Зеркало PROVIDERS из test_patient_context_contract.py (санкционированные входы в память).
_PROVIDERS = (
    "patient_context", "build_patient_brief", "recent_notes", "reasoning_block",
    "chat_facts", "_build_gp_context", "get_system_prompt",
)


def test_symptom_intake_stays_memory_free():
    """Движок элиситации не тянет память пациента — иначе заякорит мотор расхождения
    (док, §«Не давать набору гипотез схлопнуться»). Если провод памяти появился — это
    смена классификации: осознанно сними из ALLOWLIST контракта или пересмотри замысел."""
    src = (ROOT / "symptom_intake.py").read_text(encoding="utf-8")
    hit = [p for p in _PROVIDERS if p in src]
    assert not hit, (
        f"symptom_intake.py начал тянуть память пациента через {hit}. Это меняет его "
        "memory-free классификацию (docs/explanation/symptom_intake_hypothesis.md). "
        "Осознанно реши: снять из ALLOWLIST контракта или пересмотреть замысел."
    )


def _capture_save(monkeypatch) -> dict:
    """Перехватываем save_hypothesis + глушим запись в visual_case (без БД)."""
    import hai_hypotheses
    import visual_db
    seen: dict = {}

    def _fake_save(**kw):
        seen.update(kw)
        return 999

    monkeypatch.setattr(hai_hypotheses, "save_hypothesis", _fake_save)
    monkeypatch.setattr(visual_db, "link_hypothesis_to_case", lambda *a, **k: None)
    return seen


@pytest.mark.parametrize("action", [
    {"type": "handoff",
     "hypothesis": {"observation": "o", "mechanism": "m", "prediction": "p", "test": "t"},
     "candidates": [{"label": "A", "grounding": "g"}]},
    {"type": "escalate", "reason": "набор не сузился",
     "contour": {"observation": "o", "mechanism": "", "prediction": "", "test": ""},
     "candidates": [{"label": "A", "grounding": "g"}, {"label": "B", "grounding": "g"}]},
])
def test_finalize_always_defers_to_specialist(monkeypatch, action):
    """Несущая стена (док, §«Где несущая стена»): и сходимость (handoff), и тупик
    (escalate) уходят живому врачу как needs_specialist с trigger='symptom_intake'.
    Если движок начнёт эмитить вывод в обход врача — падаем."""
    import symptom_intake as si

    seen = _capture_save(monkeypatch)
    si.finalize_to_specialist(1, action)
    assert seen.get("resolution_type") == "needs_specialist", (
        f"symptom_intake обошёл несущую стену: resolution_type={seen.get('resolution_type')!r}")
    assert seen.get("trigger") == "symptom_intake"
