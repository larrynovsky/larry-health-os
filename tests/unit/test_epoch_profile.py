"""Профиль по эпохам обязан отличать связь ВНУТРИ периода от тренда МЕЖДУ периодами.

Связь всего ряда может возникнуть из сдвига между периодами даже при слабой
связи внутри каждого периода. Независимо сгенерированные ряды ниже отделяют
эти механизмы без личной калибровки.

Мутации, на которых каждый тест обязан покраснеть, названы в теле тестов.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.unit

import correlation_gate as cg

N_EP, L_EP = 6, 120          # шесть эпох по 120 дней — все проходят допуск


def _epochs(n=N_EP, length=L_EP):
    return [np.arange(i * length, (i + 1) * length) for i in range(n)]


def _rng():
    return np.random.default_rng(20260731)


# ── Предмет: тренд между периодами не должен выживать после разреза ──

def test_trend_between_periods_does_not_survive_the_split():
    """М-E3: убрать центрирование внутри эпохи — и уровень эпохи потечёт в профиль.

    Внутри каждой эпохи связи НЕТ (независимый шум), но обе переменные от эпохи к эпохе
    ползут вверх. Общий |r| высокий; профиль обязан быть около нуля.
    """
    g = _rng()
    epi = _epochs()
    a = np.concatenate([g.normal(0, 1, L_EP) + 6.0 * i for i in range(N_EP)])
    b = np.concatenate([g.normal(0, 1, L_EP) + 6.0 * i for i in range(N_EP)])
    overall = float(np.corrcoef(a, b)[0, 1])
    n, med, weak, _sign = cg._epoch_summary(cg._epoch_profile(a, b, epi, 0.0, 1.0))
    assert overall > 0.85, f"предпосылка теста: общий r должен быть высоким, получено {overall}"
    assert n == N_EP
    assert abs(med) < 0.25, (
        f"внутри периода связи нет, а профиль показывает {med} при общем {overall:.3f} — "
        "разрез по периодам перестал разводить связь и тренд"
    )


def test_real_link_inside_every_period_survives_the_split():
    """Позитивный контроль. Профиль, который занижает ВСЁ, бесполезен так же, как
    профиль, который ничего не занижает."""
    g = _rng()
    epi = _epochs()
    a = np.concatenate([g.normal(0, 1, L_EP) + 6.0 * i for i in range(N_EP)])
    b = a + g.normal(0, 0.45, len(a))
    n, med, weak, sign = cg._epoch_summary(cg._epoch_profile(a, b, epi, 0.0, 1.0))
    assert n == N_EP
    assert med > 0.75, f"связь есть в каждом периоде, а медиана профиля {med}"
    assert weak > 0.6, f"слабейшая эпоха {weak} — связь обязана держаться ВЕЗДЕ"
    assert sign == 1.0, f"настоящая связь держит знак в каждой эпохе, а доля {sign}"


# ── Допуск эпохи: те же правила, что у strat_hi ──

def test_short_epoch_is_not_admitted():
    """М-E1: снять порог L≥16 — и короткая эпоха начнёт голосовать наравне с годом.

    Длина ровно 14 — между полом перекрытия (12) и порогом длины (16). Это не
    придирка к числу: при длине 10 эпоху отсекал бы пол перекрытия, и тест краснел бы
    на ЧУЖОМ звене, оставляя порог длины без сторожа. Первая редакция теста была
    именно такой, и мутационный стенд это показал.
    """
    g = _rng()
    a = g.normal(0, 1, 200)
    b = g.normal(0, 1, 200)
    epi = [np.arange(0, 120), np.arange(120, 134)]      # вторая — 14 дней, перекрытие полное
    prof = cg._epoch_profile(a, b, epi, 0.0, 1.0)
    assert [ei for ei, _, _ in prof] == [0], (
        "эпоха короче 16 дней обязана не попадать в профиль: на ней |r| — шум, "
        "и он утянул бы и медиану, и слабейшую"
    )


def test_inert_pair_epoch_is_gated_out():
    """М-E2: снять ярусный гейт τ̂/L — и эпоха, короткая ОТНОСИТЕЛЬНО инертности пары,
    войдёт в профиль. Допуск обязан совпадать со стратифицированной семьёй."""
    g = _rng()
    a = g.normal(0, 1, 240)
    b = g.normal(0, 1, 240)
    epi = [np.arange(0, 120), np.arange(120, 160)]      # 120 и 40 дней
    tau = 12.0                                          # τ̂/L = 0.1 и 0.3
    prof = cg._epoch_profile(a, b, epi, tau, 0.15)
    assert [ei for ei, _, _ in prof] == [0], (
        f"при τ̂={tau} эпоха из 40 дней даёт τ̂/L=0.3 > c*=0.15 и обязана быть исключена"
    )


def test_overlap_floor_drops_the_epoch():
    """Эпоха достаточной длины, но почти вся в пропусках — не измерение, а видимость."""
    g = _rng()
    a = g.normal(0, 1, 240)
    b = g.normal(0, 1, 240)
    b[130:] = np.nan                                    # во второй эпохе остаётся 10 точек
    prof = cg._epoch_profile(a, b, [np.arange(0, 120), np.arange(120, 240)], 0.0, 1.0)
    assert [ei for ei, _, _ in prof] == [0]


# ── Сводка ──

def test_weakest_is_by_magnitude_not_by_sign():
    """М-E4: `min(rs, key=abs)` → `min(rs)`.

    Вопрос профиля — «держится ли связь ВЕЗДЕ», и отвечает на него самая слабая эпоха.
    Арифметический минимум вернул бы сильную отрицательную и назвал бы её слабейшей.
    """
    prof = [(0, 100, 0.05), (1, 100, -0.60), (2, 100, 0.40)]
    n, med, weak, sign = cg._epoch_summary(prof)
    assert n == 3 and weak == 0.05, f"слабейшая должна быть 0.05, получено {weak}"
    assert med == 0.05
    assert sign == 0.667, f"знак медианы (+) совпадает в 2 из 3 эпох, получено {sign}"


def test_empty_profile_is_honest():
    """Ни одной допущенной эпохи — это «не измерено», а не «связь ноль»."""
    n, med, weak, sign = cg._epoch_summary([])
    assert n == 0 and np.isnan(med) and np.isnan(weak) and np.isnan(sign)


def test_sign_share_separates_stable_from_flapping():
    """Независимые профили различаются устойчивостью знака.
    Медиана сама по себе не описывает частоту смены знака.

    М-S1: якорь по общему r вместо медианы / доля большинства без якоря — тест краснеет
    на несимметричном профиле. М-S2: `>=` вместо строгого сравнения нуля — краснеет
    test_zero_r_is_not_a_sign.
    """
    stable = [(i, 80, r) for i, r in enumerate((0.21, 0.28, 0.33, 0.37, 0.46, 0.54, 0.58, 0.62))]
    flappy = [(i, 80, r) for i, r in enumerate((-0.27, 0.16, 0.22, -0.11, 0.29, 0.32, 0.38, 0.47))]
    *_, sign_stable = cg._epoch_summary(stable)
    *_, sign_flappy = cg._epoch_summary(flappy)
    assert sign_stable == 1.0, f"один знак во всех восьми эпохах: {sign_stable}"
    assert sign_flappy == 0.75, f"один знак в шести из восьми эпох: {sign_flappy}"
    assert sign_stable > sign_flappy, "доля знака перестала разводить устойчивость и шум"


def test_zero_r_is_not_a_sign():
    """r = 0 не несёт знака: щедрость в нуле завышала бы устойчивость. Медиана ровно 0
    даёт долю 0 — профиль без знака не может быть «устойчив по знаку»."""
    n, med, weak, sign = cg._epoch_summary([(0, 100, 0.3), (1, 100, 0.0), (2, 100, 0.2)])
    assert sign == 0.667, f"нулевая эпоха не голосует за знак, получено {sign}"
    n, med, weak, sign = cg._epoch_summary([(0, 100, 0.0)])
    assert med == 0.0 and sign == 0.0


def test_gate2_now_blocks_via_calibrated_threshold():
    """ОБРАЩЕНО 2026-08-08 (решение владельца «Global»): Gate 2 из ИЗМЕРЕНИЯ стал БЛОКОМ.
    Прежний инвариант «профиль не трогает gate_pass» ретайрнут — порог откалиброван
    (probe_calibrate_two_component, zero-false 0.225/s=1.0) и владелец включил блок.

    Сторож НАПИСАНИЯ: профиль гейтит ЧЕРЕЗ gate2_fail и пороги gate2_profile (signal_family),
    а НЕ хардкодом и НЕ втихую. Поведенческий оракул (следствие, не форма) —
    tests/unit/test_correlation_gate.py::test_gate2_blocks_weak_and_unstable.
    Break-back (отвязать gate_pass от gate2_fail или захардкодить порог) → тест краснеет.
    """
    import inspect
    src = inspect.getsource(cg._gate_daily)
    assert 'df["gate2_fail"] = _gate2_blocked(df["r_epoch_median"], df["r_epoch_sign_share"])' in src, \
        "Gate 2 не считается из профиля через _gate2_blocked"
    gp = src.split('df["gate_pass"] =')[1].split("\n")[0:2]
    assert any("gate2_fail" in ln for ln in gp), "gate_pass не зависит от gate2_fail — блок не подключён"
    hsrc = inspect.getsource(cg._gate2_blocked)
    for tok in ("GATE2_R_MIN", "GATE2_SIGN_MIN", "isnan"):
        assert tok in hsrc, f"_gate2_blocked без {tok} — порог из gate2_profile / N/A на NaN обязательны"


# ── Доставка: число обязано ДОЙТИ до читателя, а не осесть в колонке ──
# Обнаружение без доставки этот проект уже проходил (рельса warn была мертва 13 дней).
# Профиль, посчитанный и не показанный, — ровно тот же класс.

_ITEM = {"a": "fixture_q", "b": "fixture_r", "r": 0.56, "p": 0.003,
         "epochs_n": 8, "r_epoch_median": 0.275, "r_epoch_weakest": -0.11,
         "r_epoch_sign_share": 0.75}


def test_label_is_a_fact_not_a_verdict():
    """Слова формулировки — единый источник. Оценочного слова в ней быть не должно:
    порог «держится/не держится» не откалиброван, и вердикт без основания хуже числа.

    Тест уже сработал по делу: первая редакция писала «слабейший», и это оценка, а не
    измерение. Заменено на «ближайший к нулю» — буквально то, что вычисляется.
    """
    s = cg.epoch_label(_ITEM)
    assert "по периодам (8)" in s and "+0.28" in s and "-0.11" in s
    assert "тот же знак в 6/8" in s, f"доля знака посчитана, но до читателя не доехала: {s}"
    for word in ("слаб", "ненадёж", "сомнит", "зато", "лишь", "всего"):
        assert word not in s.lower(), f"оценочное слово «{word}» — это вердикт, а не измерение"


def test_label_without_sign_share_renders_as_before():
    """Снимок веры, посчитанный до 2026-08-04, поля доли знака не имеет — рендер обязан
    не упасть и не выдумать число (backward-compat, тот же класс, что старый снимок)."""
    item = {k: v for k, v in _ITEM.items() if k != "r_epoch_sign_share"}
    s = cg.epoch_label(item)
    assert "по периодам (8)" in s and "знак" not in s


def test_label_is_empty_for_old_snapshot():
    """Старый снимок веры без профиля → рендер как раньше (backward-compat)."""
    assert cg.epoch_label({"a": "x", "b": "y", "r": 0.5}) == ""
    assert cg.epoch_label({}) == ""
    assert cg.epoch_label(None) == ""
    assert cg.epoch_label({"r_epoch_median": 0.1, "epochs_n": 0}) == ""


def test_constitution_render_carries_the_profile(monkeypatch):
    """Предмет: строка конституции, которую реально читает ИИ, содержит числа профиля."""
    import belief_contract, generate_constitutions as gc
    belief = {"accepted": True, "reason": "ok", "generated_at": "2032-04-12",
              "age_days": 0, "run_failed": False, "failed_at": None, "error": None,
              "run_id": "t", "receipt_run_id": "t", "data_changed_at": None,
              "stale_vs_data": False,
              "data": {"top_correlations": [dict(_ITEM)]}}
    monkeypatch.setattr(belief_contract, "read_belief", lambda *a, **k: belief)
    out = gc._get_longitudinal_context()
    assert "по периодам (8)" in out, (
        "профиль посчитан, но до конституции не доехал — обнаружение без доставки:\n" + out
    )


def test_profile_is_shown_even_for_quarantined_pair(monkeypatch):
    """Карантин — вердикт о СОСТАВЕ pass-set; профиль — факт о ДАННЫХ. Первый не отменяет
    второго, иначе именно у подозрительной пары читатель числа не увидит."""
    import belief_contract, generate_constitutions as gc
    item = dict(_ITEM, online_status="pending_adjudication")
    belief = {"accepted": True, "reason": "ok", "generated_at": "2032-04-12",
              "age_days": 0, "run_failed": False, "failed_at": None, "error": None,
              "run_id": "t", "receipt_run_id": "t", "data_changed_at": None,
              "stale_vs_data": False, "data": {"top_correlations": [item]}}
    monkeypatch.setattr(belief_contract, "read_belief", lambda *a, **k: belief)
    out = gc._get_longitudinal_context()
    assert "состав менялся" in out and "по периодам (8)" in out, (
        "у карантинной пары профиль пропал — читатель лишён чисел там, где они нужнее:\n" + out
    )


def test_gp_context_render_carries_the_profile(monkeypatch):
    """Второй ИИ-читатель. Проверяется отдельно от конституций намеренно: единый источник
    СЛОВ (`epoch_label`) не гарантирует, что оба читателя его зовут — ровно этот разрыв
    («функция исправна, её никто не вызывает») уже стоил проекту тринадцати дней молчания
    рельсы warn. Оракул должен щупать ПУТЬ, а не слой."""
    import belief_contract, gp_context
    belief = {"accepted": True, "reason": "ok", "generated_at": "2032-04-12",
              "age_days": 0, "run_failed": False, "failed_at": None, "error": None,
              "run_id": "t", "receipt_run_id": "t", "data_changed_at": None,
              "stale_vs_data": False, "data": {"top_correlations": [dict(_ITEM)]}}
    monkeypatch.setattr(gp_context, "read_belief", lambda *a, **k: belief, raising=False)
    monkeypatch.setattr(belief_contract, "read_belief", lambda *a, **k: belief)
    out = "\n".join(gp_context._build_longitudinal_correlations_block())
    assert "по периодам (8)" in out, (
        "профиль не доехал до gp_context — второй читатель остался без чисел:\n" + out
    )
