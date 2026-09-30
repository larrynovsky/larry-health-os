"""Семья v9: время засыпания/подъёма — члены поиска связей (решение владельца 25.09).

Столбцы sleep_start/sleep_end — ISO-строки. Без перевода в минуты `pd.to_numeric` молча дал бы
NaN на всех днях, и член семьи никогда бы не тестировался — «не тестировалась» под видом
«незначима». Оракул: загрузчик гейта отдаёт минуты от полудня, 23:50 и 00:20 — рядом."""
import pytest

pytestmark = pytest.mark.unit


def test_clock_members_are_declared_with_structural_pairs():
    import signal_family as sf
    assert {"sleep_start", "sleep_end"} <= set(sf.DAILY_METRICS)
    import yaml
    d = yaml.safe_load(open(sf._PATH, encoding="utf-8"))
    pairs = {tuple(p) for p in d["algorithmic_derived_pairs"]}
    assert {("sleep_start", "sleep_inbed"), ("sleep_end", "sleep_inbed")} <= pairs


def test_gate_loader_turns_clock_into_minutes_from_noon(db, monkeypatch):
    import longitudinal_analysis as la
    # у longitudinal свой DB_PATH (вычислен на импорте) — фикстура health_db его не подменяет;
    # без этой строки тест читал канон/чужую базу и зеленел от окружения (§20, пойман на Studio)
    monkeypatch.setattr(la, "DB_PATH", db.path)
    db.add_daily_metrics("2026-08-30", sleep_start="2026-08-29T23:50:00+03:00",
                         sleep_end="2026-08-30T07:10:00+03:00", sleep_total=7.0)
    db.add_daily_metrics("2026-08-31", sleep_start="2026-08-31T00:20:00+03:00",
                         sleep_end="2026-08-31T08:00:00+03:00", sleep_total=7.5)
    df = la.load_daily_df().set_index("date")
    assert list(df["sleep_start"]) == [710, 740]      # через полночь — соседи, не 23:50 vs 0:20
    assert list(df["sleep_end"]) == [1150, 1200]
