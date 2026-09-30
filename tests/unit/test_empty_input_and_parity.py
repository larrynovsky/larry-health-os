"""Пустой вход и паритет выборки — VG-R4-06 / VG-R4-07.

Оба находки об одном: числа и отказы, попадающие в веру, должны быть про ОДНО И ТО ЖЕ.
`lab_obs_n` и `producer_obs_n` — единственный числовой контроль того, что канонизация имён
у писателя и у гейта сошлась; если они считают по разным множествам, контроль врёт молча.
А `KeyError` на пустом входе означал, что «данных не набралось» неотличимо от «код сломан».
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import correlation_gate as cg               # noqa: E402
import longitudinal_analysis as la          # noqa: E402


# ─────────────────── VG-R4-07: пустой вход проходит путь, а не роняет его ───────────────────

@pytest.mark.parametrize("fn,args", [
    ("correlation_matrix", lambda: (pd.DataFrame({"date": pd.to_datetime([])}),)),
    ("lagged_correlations", lambda: (pd.DataFrame({"date": pd.to_datetime([])}),)),
])
def test_empty_input_returns_typed_frame_not_keyerror(fn, args):
    out = getattr(la, fn)(*args())
    assert isinstance(out, pd.DataFrame) and out.empty
    assert "spearman_r" in out.columns, "колонки обязаны быть — по ним ходит гейт"


def test_lab_correlations_on_empty_labs_does_not_crash():
    """Раньше: `KeyError: 'spearman_r'` ДО гейта и ДО квитанции. Прогон умирал без следа —
    ни строки веры, ни записи отказа: «лаб-данных нет» выглядело как крэш неизвестной природы.
    """
    daily = pd.DataFrame({"date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                          "hrv": [40.0, 42.0]})
    labs = pd.DataFrame({"date": pd.to_datetime([]), "test_name": [], "value": []})
    out = la.lab_metric_correlations(daily, labs)
    assert out.empty and "spearman_r" in out.columns
    assert out.attrs.get("producer_obs_n") is not None, "провенанс нужен и на пустом входе"


def test_empty_frame_dtypes_allow_boolean_masks():
    """Типы, а не только имена: читатели делают `df[df["strong"] & df["significant"]]`.
    На object-колонках это ведёт себя иначе, чем на bool, и тест на пустом входе перестал бы
    говорить что-либо о боевом пути."""
    out = la.correlation_matrix(pd.DataFrame({"date": pd.to_datetime([])}))
    assert out["significant"].dtype == bool and out["strong"].dtype == bool
    assert out[out["strong"] & out["significant"]].empty


# ─────────────────── VG-R4-06: гейт и producer считают одно множество ───────────────────

def test_gate_obs_count_drops_nulls_like_producer():
    """Аналит с одним измеренным и одним пустым значением: producer видит 1 наблюдение
    (`dropna(subset=["value"])`), гейт видел 2. Оба числа ехали в веру как «сколько данных
    за этим выводом» — и были про разные вещи."""
    name = cg._sf.LAB_METRICS[0]
    labs = pd.DataFrame({"test_name": [name, name], "value": [1.0, None]})
    assert cg._lab_obs_counts(labs, [name])[name] == 1


def test_parity_holds_end_to_end_with_nulls():
    """Паритет проверяется НА ПУТИ: то же множество лаб-строк отдаётся обоим считающим."""
    name = cg._sf.LAB_METRICS[0]
    daily = pd.DataFrame({"date": pd.to_datetime(["2026-07-01"]), "hrv": [40.0]})
    labs = pd.DataFrame({"date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                         "test_name": [name, name], "value": [1.0, None]})
    prod = la.lab_metric_correlations(daily, labs).attrs.get("producer_obs_n", {})
    gate = cg._lab_obs_counts(labs, [name])
    assert prod.get(name) == gate.get(name), \
        f"producer={prod.get(name)} != гейт={gate.get(name)} — считали разные выборки"


def test_gate_obs_count_still_counts_measured_values():
    """Негативный контроль: dropna не должен обнулить нормальный случай."""
    name = cg._sf.LAB_METRICS[0]
    labs = pd.DataFrame({"test_name": [name, name], "value": [1.0, 2.0]})
    assert cg._lab_obs_counts(labs, [name])[name] == 2
