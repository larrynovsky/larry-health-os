"""_write_passset_snapshot (Фаза 4 предохранитель) — append-only снимок членства pass-set.

Т: членство из cg (не top_correlations), append-only + один-на-дату, cap, sha-штамп,
gate_applied=False → не пишет. Импорт longitudinal тянет numpy → на MacBook ПРОПУСК.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
pytest.importorskip("numpy")
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import longitudinal_analysis as L
except Exception as e:  # noqa: BLE001
    pytest.skip(f"longitudinal_analysis не импортируется здесь: {e}", allow_module_level=True)


def _cg(rows):
    """rows: [(a,b,gate_pass,a_lever)]."""
    return pd.DataFrame([{"metric_a": a, "metric_b": b, "gate_pass": gp, "a_lever": al}
                         for a, b, gp, al in rows])


def _meta(lagged=None):
    return {"gate_applied": True, "stratified": {"lagged": lagged or []}}


def test_members_from_cg_not_capped():
    cg = _cg([("sleep_total", "hrv", True, True), ("steps", "hrv", True, False),
              ("x", "y", False, False)])
    m = L._passset_members(cg, _meta([{"predictor": "hrv", "target": "sleep_total", "lag_days": 2}]))
    assert m["D"] == ["sleep_total×hrv", "steps×hrv"]
    assert m["A"] == ["sleep_total×hrv"]           # только a_lever
    assert m["q_lag"] == ["hrv→sleep_total+2д"]


def test_append_only_and_one_per_date(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 24))
    monkeypatch.setattr(L, "_head_sha", lambda: "aaa")
    cg = _cg([("a", "b", True, False)])
    # Контракт возврата изменён ревью R4: был bool, стало СОСТОЯНИЕ ПЕРЕХОДА
    # {"ok","corrupt","conflict","skipped"}. Причина: bool вызывающий игнорировал, и отказ
    # записи был неотличим от успеха — вера уходила поверх непереведённого состояния.
    assert L._write_passset_snapshot(cg, _meta(), artifact=art)["ok"] is True
    # повтор в тот же день → замещает, не задваивает
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("c", "d", True, False)]), _meta(), artifact=art)
    hist = json.loads(art.read_text())
    assert len(hist) == 1 and hist[-1]["members"]["D"] == ["a×b", "c×d"]
    # новый день → append
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 31))
    L._write_passset_snapshot(cg, _meta(), artifact=art)
    hist = json.loads(art.read_text())
    assert len(hist) == 2 and hist[-1]["date"] == "2026-07-31"
    assert hist[-1]["head_sha"] == "aaa"


def test_history_capped(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    for i in range(L.PASSSET_HISTORY_MAX + 5):
        monkeypatch.setattr(L, "get_today", lambda i=i: date(2026, 1, 1) + __import__("datetime").timedelta(days=i))
        L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    hist = json.loads(art.read_text())
    assert len(hist) == L.PASSSET_HISTORY_MAX


def test_skips_when_gate_not_applied(tmp_path):
    art = tmp_path / "h.json"
    st = L._write_passset_snapshot(_cg([("a", "b", True, False)]),
                                   {"gate_applied": False}, artifact=art)
    # `skipped`, а не просто `ok=False`: вызывающий обязан отличать «писать было нечего»
    # от «писать пытались и не смогли» — иначе он запишет отказ дважды под разными причинами.
    assert st["ok"] is False and st["skipped"] is True
    assert not art.exists()


def test_missing_columns_no_crash(monkeypatch, tmp_path):
    """Тенант/старый прогон без a_lever → A пустой, не крэш."""
    cg = pd.DataFrame([{"metric_a": "a", "metric_b": "b", "gate_pass": True}])  # нет a_lever
    m = L._passset_members(cg, _meta())
    assert m["D"] == ["a×b"] and m["A"] == []


# ── flicker_vs_prev: единый источник (Коммит B), sha-гвард в ПИСАТЕЛЕ ──────────
def test_flicker_stored_same_sha(monkeypatch, tmp_path):
    """Писатель считает flicker_vs_prev против предыдущего прогона того же sha."""
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("c", "d", True, False)]), _meta(), artifact=art)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("e", "f", True, False)]), _meta(), artifact=art)
    fv = json.loads(art.read_text())[-1]["flicker_vs_prev"]
    assert fv["D"]["entered"] == ["e×f"] and fv["D"]["left"] == ["c×d"]


def test_flicker_survives_sha_drift(monkeypatch, tmp_path):
    """⭐ ПОЗИТ-КОНТРОЛЬ правки 2026-07-25 (заменил test_flicker_empty_across_sha).

    Старый ключ сравнимости требовал СОВПАДЕНИЯ head_sha двух снимков. Замер: между воскресными
    прогонами ложится 104–298 коммитов → sha всегда другой → prev не находился НИКОГДА, детектор
    два месяца рапортовал «стабильно», карантин был пуст по конструкции. Тот тест был зелёным
    ИМЕННО на сломанном поведении — он морозил авторское допущение «sha меняется вместе с гейтом».
    Верни `h.get("head_sha") == snap["head_sha"]` в условие prev → этот тест краснеет."""
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", tmp_path / "brk.txt")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    monkeypatch.setattr(L, "_head_sha", lambda: "OLD")
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("c", "d", True, False)]), _meta(), artifact=art)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    monkeypatch.setattr(L, "_head_sha", lambda: "NEW")   # неделя коммитов — норма, не смена гейта
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("e", "f", True, False)]), _meta(), artifact=art)
    last = json.loads(art.read_text())[-1]
    assert last["flicker_vs_prev"]["D"] == {"entered": ["e×f"], "left": ["c×d"]}
    assert last["prev"] == {"date": "2026-07-19", "head_sha": "OLD"}   # контекст для читателя алерта


def test_epoch_break_marker_suppresses_flicker(monkeypatch, tmp_path):
    """Маркер эпохи: правил решающее правило — положи файл, и сдвиг НЕ читается как мерцание.
    Одноразовый (закрывает ровно одну эпоху) → файл удаляется. Забыл поставить → ложная тревога,
    опознаётся за секунды; тихой ветки у маркера нет — в отличие от снятого sha-ключа."""
    art = tmp_path / "h.json"
    mark = tmp_path / "brk.txt"
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", mark)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("c", "d", True, False)]), _meta(), artifact=art)
    mark.write_text("Ф3 BH→BY: порог семьи изменён методологически", encoding="utf-8")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)   # членство упало
    last = json.loads(art.read_text())[-1]
    assert last["flicker_vs_prev"] == {}
    assert "BH→BY" in last["epoch_break"]
    assert not mark.exists()   # одноразовый


def test_flicker_ignores_same_day_rerun(monkeypatch, tmp_path):
    """Повтор в тот же день не считается «предыдущим прогоном» — flicker против ВЧЕРАШНЕГО."""
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("x", "y", True, False)]), _meta(), artifact=art)
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("x", "y", True, False)]), _meta(), artifact=art)  # повтор
    fv = json.loads(art.read_text())[-1]["flicker_vs_prev"]
    assert fv["D"]["entered"] == ["x×y"]   # против 19-го, не против сегодняшнего повтора


def test_entered_pairs_reads_arrival_event(monkeypatch, tmp_path):
    """СОБЫТИЕ прибытия (было `_quarantined_pairs`, переименовано 2026-07-26): теперь эта
    функция только читает `entered` из снимка, а состояние карантина живёт в БД —
    иначе оно снималось само через один стабильный прогон (P1-03)."""
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("e", "f", True, True)]),
                              _meta([{"predictor": "hrv", "target": "steps", "lag_days": 1}]), artifact=art)
    pairs = {e["pair"] for e in L._entered_pairs(artifact=art)}
    assert "e×f" in pairs and "hrv→steps+1д" in pairs and "a×b" not in pairs


# ── Находки внешнего ревью 2026-07-25 ─────────────────────────────────────────
def test_corrupt_history_is_preserved_and_flagged(monkeypatch, tmp_path):
    """P1: раньше битый артефакт МОЛЧА заменялся новой историей — читатель видел валидный
    свежий файл с одним снимком и говорил «стабильно». Теперь: повреждённое сохраняется рядом,
    факт сброса едет в снимке. Убери ветку `_reset` → тест краснеет."""
    art = tmp_path / "h.json"
    art.write_text("{ broken json", encoding="utf-8")
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", tmp_path / "brk.txt")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    st = L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    assert st["ok"] is True, "лечение состоялось — снимок записан"
    # VG-R4-01: успех ЗАПИСИ не гасит факт порчи. Раньше вызывающий видел только `True` и
    # публиковал веру так, будто ряд цел; теперь `corrupt` едет наверх и блокирует публикацию.
    assert st["corrupt"], "факт сброса истории обязан дойти до решения о публикации"
    snap = json.loads(art.read_text())[-1]
    assert "history_reset" in snap, snap
    assert (tmp_path / "h.corrupt-2026-07-26.json").exists()          # улика сохранена
    assert (tmp_path / "h.corrupt-2026-07-26.json").read_text() == "{ broken json"


def test_epoch_marker_survives_failed_write(monkeypatch, tmp_path):
    """P2: маркер потреблялся ДО записи — при сбое сгорал, не подавив ни одного diff.
    Теперь unlink только после успешной записи."""
    mark = tmp_path / "brk.txt"
    mark.write_text("методология", encoding="utf-8")
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", mark)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    # Сбой ИМЕННО записи: родитель — обычный файл, поэтому mkdir/write упадут, а ветка
    # «битый артефакт» не сработает (path.exists() == False). Каталог как artifact не годится:
    # после фикса P1-3 он переименовывается в .corrupt и запись УДАЁТСЯ.
    blocker = tmp_path / "afile.txt"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "h.json"
    assert L._write_passset_snapshot(_cg([("a", "b", True, False)]),
                                     _meta(), artifact=bad)["ok"] is False
    assert mark.exists(), "маркер потрачен, хотя снимок не записан"


# ── Находки внешнего ревью, раунд 2 (2026-07-26) ─────────────────────────────
def test_canonical_never_disappears_on_write_failure(monkeypatch, tmp_path):
    """F2-02: раньше повреждённый файл ПЕРЕИМЕНОВЫВАЛСЯ до записи нового — между rename и записью
    канонического файла не существовало, и параллельный читатель видел отсутствие истории как
    норму. Теперь при сбое записи прежний файл остаётся на месте (на нём читатель кричит)."""
    art = tmp_path / "h.json"
    art.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", tmp_path / "brk.txt")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))

    def _boom(_p, _payload):
        raise OSError(28, "no space")
    monkeypatch.setattr(L, "_atomic_write_json", _boom)
    assert L._write_passset_snapshot(_cg([("a", "b", True, False)]),
                                     _meta(), artifact=art)["ok"] is False
    assert art.read_text() == "{ broken", "канонический файл исчез или подменён при сбое записи"
    assert not list(tmp_path.glob("*.corrupt-*")), "улика создана до успешной записи канона"


def test_two_corruptions_same_day_keep_both(monkeypatch, tmp_path):
    """F2-05: обе улики одной даты должны сохраниться — раньше вторая затирала первую."""
    art = tmp_path / "h.json"
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", tmp_path / "brk.txt")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    art.write_text("FIRST-CORRUPTION", encoding="utf-8")
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    art.write_text("SECOND-CORRUPTION", encoding="utf-8")
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    saved = sorted(p.read_text() for p in tmp_path.glob("*.corrupt-*"))
    assert saved == ["FIRST-CORRUPTION", "SECOND-CORRUPTION"], saved


def test_history_reset_survives_same_day_rerun(monkeypatch, tmp_path):
    """Найдено мной при разборе раунда 2: повторный прогон в тот же день замещал снимок и СТИРАЛ
    `history_reset` — фикс F2-02 держался ровно один прогон, ручной перезапуск возвращал тишину."""
    art = tmp_path / "h.json"
    art.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", tmp_path / "brk.txt")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)
    L._write_passset_snapshot(_cg([("a", "b", True, False)]), _meta(), artifact=art)   # повтор
    assert "history_reset" in json.loads(art.read_text())[-1]


def test_marker_is_one_shot_even_if_unlink_fails(monkeypatch, tmp_path):
    """F2-04: «одноразовый» маркер становился многоразовым, если unlink не удался — тот же файл
    подавлял diff каждую неделю. Одноразовость держится на отпечатке в снимке, не на unlink."""
    art = tmp_path / "h.json"
    mark = tmp_path / "brk.txt"
    mark.write_text("one-shot", encoding="utf-8")

    class _NoUnlink(type(mark)):
        def unlink(self, *a, **k):
            raise OSError(1, "operation not permitted")

    monkeypatch.setattr(L, "EPOCH_BREAK_MARK", _NoUnlink(str(mark)))
    monkeypatch.setattr(L, "_head_sha", lambda: "s")
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 19))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("c", "d", True, False)]), _meta(), artifact=art)
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 26))
    L._write_passset_snapshot(_cg([("a", "b", True, False), ("g", "h", True, False)]), _meta(), artifact=art)
    last = json.loads(art.read_text())[-1]
    assert mark.exists(), "предпосылка теста: unlink должен был провалиться"
    assert last["flicker_vs_prev"], "второй прогон снова подавлен тем же маркером"
    assert last["flicker_vs_prev"]["D"]["entered"] == ["g×h"], last


# --- 28.09 (C-79, второй экземпляр): структурная пара не член pass-set ------------------------------

def _structural(monkeypatch, *pairs):
    """Реестр подменяется явно: тест судит механизм среза, а не текущее содержимое yaml (§20)."""
    monkeypatch.setattr(L._sf, "STRUCTURAL_PAIRS", {frozenset(p) for p in pairs})


def test_members_drop_structural_in_every_family(monkeypatch):
    _structural(monkeypatch, ("hrv", "recovery_high_min"), ("hrv", "sleep_total"))
    cg = _cg([("hrv", "recovery_high_min", False, True), ("steps", "hrv", True, True)])
    m = L._passset_members(cg, _meta([{"predictor": "hrv", "target": "sleep_total", "lag_days": 2},
                                      {"predictor": "steps", "target": "sleep_total", "lag_days": 1}]))
    assert m["A"] == ["steps×hrv"], m
    assert m["D"] == ["steps×hrv"], m
    assert m["q_lag"] == ["steps→sleep_total+1д"], m


def test_prev_snapshot_structural_pair_is_not_left(monkeypatch):
    """Ключевой момент: снимок, записанный ДО объявления (или до 28.09), содержит структурную пару.
    Без среза прошлого снимка объявление выглядит как «пара ушла» — ложный алерт мерцания."""
    _structural(monkeypatch, ("hrv", "recovery_high_min"))
    prev = {"D": [], "A": ["hrv×recovery_high_min", "steps×hrv"], "q_lag": []}
    cur = {"D": [], "A": ["steps×hrv"], "q_lag": []}
    assert L._flicker_diff(prev, cur) == {}


def test_structural_arrival_is_not_entered(monkeypatch):
    """Вход структурной пары не прибытие → не попадает в карантин (он читает entered)."""
    _structural(monkeypatch, ("hrv", "recovery_high_min"))
    diff = L._flicker_diff({"A": []}, {"A": ["hrv×recovery_high_min", "a×b"]})
    assert diff == {"A": {"entered": ["a×b"], "left": []}}
