"""Два прохода чтения фото — две разные модели у каждого поставщика (решение владельца 02.10,
вариант А: у DeepSeek одна модель видит картинки — фото у него не работает, а не читается дважды
одной моделью; общая ошибка двух чтений одной модели прошла бы сверку).

Модели по умолчанию: у anthropic — hai_core.MODEL_DEFAULTS (ревью 02.10: в профиле их нет, и
сторож его пропускал), у остальных — role_defaults профиля. Модели из базы тенанта и цепочки
стережёт рантайм: lab_recognizer отказывает при одной модели на оба прохода (test_model_chain)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _defaults() -> dict:
    import hai_core
    prof = json.loads((ROOT / "methodology" / "llm_providers.json").read_text(encoding="utf-8"))
    out = {"anthropic": dict(hai_core.MODEL_DEFAULTS)}
    out.update({n: p["role_defaults"] for n, p in prof.items()
                if isinstance(p, dict) and p.get("role_defaults")})
    return out


def test_opus_and_sonnet_defaults_differ_for_every_provider():
    d = _defaults()
    assert set(d) >= {"anthropic", "openai", "gemini", "deepseek"}, sorted(d)
    bad = [f"{name}: {rd['opus']}" for name, rd in d.items() if not (rd["opus"] != rd["sonnet"])]
    assert not bad, f"одна модель на оба прохода чтения фото: {bad}"
