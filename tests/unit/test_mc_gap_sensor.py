"""check_mc_gap (Коммит3) — freshness-гвард + доставка (RST-триада).

5 веток читателя: свежий+флаги → warn; свежий+пусто → молчит; устаревший → liveness-warn;
отсутствует → молчит (покрыт check_longitudinal_freshness); битый JSON → fail-loud.
Плюс delivery-гард: warn НЕ попадает в MUTE_WARN_SUBSTRINGS (детект без доставки = нет датчика).

Импорт integrity_tests требует health_db+БД (Studio-гвард) → на MacBook ПРОПУСК целиком.
Файл-артефакт передаём напрямую (artifact=), время — monkeypatch it.today.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import integrity_tests as it
except Exception as e:  # noqa: BLE001 — health_db-гвард/нет БД: не наша среда
    pytest.skip(f"integrity_tests не импортируется здесь: {e}", allow_module_level=True)


TODAY = date(2026, 7, 24)


def _write(path: Path, stamp, families):
    path.write_text(json.dumps({"date": str(stamp), "head_sha": "abc123",
                                "mc_gap": {"families": families}}), encoding="utf-8")


def _run(monkeypatch, artifact):
    captured = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": captured.append((name, detail)))
    monkeypatch.setattr(it, "today", TODAY)
    res = it.check_mc_gap(artifact=artifact)
    return captured, res


def test_fresh_with_flag_warns(monkeypatch, tmp_path):
    art = tmp_path / "gate_mc_gap_latest.json"
    _write(art, TODAY, {"D": {"evaluated": True, "flagged": [
        {"pair": "sleep×hrv", "p": 0.001, "line": 0.0012, "mcse": 0.0009}]},
        "A": None, "q_lag": None})
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "MC-зазор" in cap[0][0]
    assert res["flagged"] and "D:sleep×hrv" in res["flagged"][0]


def test_fresh_empty_silent(monkeypatch, tmp_path):
    art = tmp_path / "a.json"
    _write(art, TODAY, {"D": {"evaluated": True, "flagged": []}, "A": None, "q_lag": None})
    cap, res = _run(monkeypatch, art)
    assert cap == []                    # зазор пуст → молчит
    assert res["flagged"] == []


def test_stale_warns_liveness(monkeypatch, tmp_path):
    art = tmp_path / "a.json"
    _write(art, date(2026, 7, 1), {"D": {"evaluated": True, "flagged": []}})  # 23д назад
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "замолк" in cap[0][0]
    assert res is None                  # устаревшему содержимому не доверяем


def test_missing_silent(monkeypatch, tmp_path):
    cap, res = _run(monkeypatch, tmp_path / "nope.json")
    assert cap == [] and res is None    # «гейт не бегал» покрыт check_longitudinal_freshness


def test_corrupt_json_fails_loud(monkeypatch, tmp_path):
    art = tmp_path / "a.json"
    art.write_text("{ не json", encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "не читается" in cap[0][0]
    assert res is None


def test_bad_stamp_fails_loud(monkeypatch, tmp_path):
    art = tmp_path / "a.json"
    _write(art, "не-дата", {"D": {"evaluated": True, "flagged": []}})
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "штамп" in cap[0][0]


def test_family_none_not_iterated(monkeypatch, tmp_path):
    """A/q_lag=None (owner-off) не роняет читатель и не даёт ложных флагов."""
    art = tmp_path / "a.json"
    _write(art, TODAY, {"D": {"evaluated": True, "flagged": []}, "A": None, "q_lag": None})
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["flagged"] == []


def test_warn_not_muted_delivery_guard():
    """Anti «детект без доставки»: реальные warn-подстроки mc_gap НЕ в MUTE-list —
    спайн triage_agent доставит их (mute-list, не whitelist). Split-brain-гард между датчиком
    и доставкой: если кто-то расширит MUTE так, что заглушит mc_gap — этот тест покраснеет."""
    ta = pytest.importorskip("triage_agent")
    labels = ["MC-зазор: 2 открытий на разрешении Монте-Карло",
              "mc_gap датчик замолк: 9д (плановый запуск 20.09 03:00 не отметился)",
              "mc_gap датчик: живость не судима",
              "mc_gap артефакт не читается"]
    delivered = ta.classify_warnings([(lbl, "detail") for lbl in labels])
    assert len(delivered) == len(labels), "все warn обязаны дойти (ни один не заглушён MUTE)"


# ── Гарды формата/будущего штампа (находки внешнего ревью 2026-07-24) ──────────
def test_non_dict_root_soft(monkeypatch, tmp_path):
    """Валидный JSON, но корень не объект → мягкий warn (как sibling passset), НЕ AttributeError."""
    art = tmp_path / "a.json"
    art.write_text(json.dumps(["не", "объект"]), encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "неформатен" in cap[0][0]
    assert res is None


def test_non_dict_families_no_crash(monkeypatch, tmp_path):
    """mc_gap.families не объект (список) → не крэш на .items(), тихо (нечего читать)."""
    art = tmp_path / "a.json"
    art.write_text(json.dumps({"date": str(TODAY), "head_sha": "s", "mc_gap": {"families": ["мусор"]}}), encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["flagged"] == []


def test_fam_data_non_dict_skipped(monkeypatch, tmp_path):
    """Данные семьи — список/скаляр (не dict) → пропуск, не крэш на .get()."""
    art = tmp_path / "a.json"
    _write(art, TODAY, {"D": ["мусор"], "A": None})
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["flagged"] == []


def test_future_stamp_warns(monkeypatch, tmp_path):
    """Штамп из будущего (age<0) → warn, содержимому не доверяет (не «свежий»)."""
    art = tmp_path / "a.json"
    _write(art, date(2026, 8, 1), {"D": {"evaluated": True, "flagged": [
        {"pair": "x×y", "p": 0.001, "line": 0.0012}]}})   # 8 дней в будущем
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "будущего" in cap[0][0]
    assert res is None


# ── Триггер BL-MCGAP-FLIP-1 (23.09): доставка через тот же ночной датчик ─────────
def test_fragile_floor_warns_with_context_pointer(monkeypatch, tmp_path):
    """D пропускает пары на шумовом полу (прецедент до 02.08) → отдельный warn со ссылкой на контекст."""
    art = tmp_path / "a.json"
    _write(art, TODAY, {"D": {"evaluated": True, "passed": 8, "B": 1000, "m": 44, "k_star": 8,
                              "flagged": []}, "A": None, "q_lag": None})
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "BL-MCGAP-FLIP-1" in cap[0][0] and "BACKLOG" in cap[0][1]
    assert res["fragile"] and res["fragile"][0].startswith("D:")


def test_fragile_warn_not_muted():
    ta = pytest.importorskip("triage_agent")
    lbl = "MC-триггер BL-MCGAP-FLIP-1: 1 семья(и) пропускают пары на шумовом полу"
    assert len(ta.classify_warnings([(lbl, "detail")])) == 1, "триггер заглушён MUTE — детект без доставки"
