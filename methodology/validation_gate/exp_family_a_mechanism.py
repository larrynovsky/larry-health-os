"""Механизм-эксперимент семьи A (стратифицированный нуль) — ВАЛИДАЦИЯ, НЕ продовый код.

Семьи нуля сравниваются на разных механизмах: общий сдвиг уровней между эпохами
и связь внутри эпох. Дискриминатор строится так, чтобы уровни со-двигались,
а внутри эпох ряды были независимы; это проверяет отделение режимного отклика.
Метод: per-эпоха masked циркулярный сдвиг (НЕ FFT-zerofill — невалиден при неравном
покрытии), нормированная корреляция r_e с центрированием по перекрытию, точное
перечисление [g,L-g); пул S=Σ w_e·r_e с весом w_e=1/Var(r_e_null) (∝ n_e).
Продовая функция `_gate_daily_stratified` — TODO (docs/explanation/null_gate_impl_plan.md; нить fdr-online).
Запуск: /opt/homebrew/bin/python3.11 methodology/validation_gate/exp_family_a_mechanism.py (только Studio — нужен канон-DB).
"""
import sqlite3, numpy as np, pandas as pd, yaml
from pathlib import Path
from scipy.stats import rankdata
rng = np.random.default_rng(0)
man = yaml.safe_load((Path(__file__).resolve().parent / 'data_manifest.yaml').read_text())
epochs = man['epochs']
metrics = ['sleep_total','sleep_deep','sleep_score','hrv']
con = sqlite3.connect(f"file:{Path.home() / 'health/data/health.db'}?mode=ro", uri=True)
df = pd.read_sql_query('SELECT date,'+','.join(metrics)+' FROM daily_metrics ORDER BY date', con, parse_dates=['date'])
con.close()
df = df.set_index('date').asfreq('D'); cal = df.index; N = len(cal)

def grank(v):
    v = np.asarray(v, float); m = ~np.isnan(v); r = np.full(len(v), np.nan)
    if m.sum(): r[m] = rankdata(v[m]); r[m] = r[m] - r[m].mean()
    return r
R = {m: grank(df[m].to_numpy(float)) for m in metrics}
g = 7
emasks = []
for e in epochs:
    s = pd.Timestamp(e['range'][0]); en = e['range'][1]; en = pd.Timestamp(en) if en else cal.max()
    mask = np.asarray((cal >= s) & (cal <= en))
    if mask.sum() >= 16: emasks.append((e['name'], mask))
def resolve(z): return R[z] if isinstance(z, str) else z

# ── семья A: per-эпоха masked циркул.сдвиг, ТОЧНОЕ перечисление, кросс-произведение (§3.1.1) ──
def epoch_tbl(ax, ay, mask):
    xc = ax[mask]; yc = ay[mask]; L = len(xc)
    if L - 2*g < 2: return None
    okx = ~np.isnan(xc)
    def cross(yv):
        ov = okx & ~np.isnan(yv)
        if ov.sum() < 12: return None
        xo = xc[ov]; yo = yv[ov]
        xo = xo - xo.mean(); yo = yo - yo.mean()
        d = np.sqrt((xo * xo).sum() * (yo * yo).sum())
        return float((xo * yo).sum() / d) if d > 0 else 0.0
    obs = cross(yc)
    if obs is None: return None
    vals = []
    for k in range(g, L - g):
        c = cross(np.roll(yc, k))
        if c is not None: vals.append(c)
    if len(vals) < 10: return None
    return obs, np.array(vals)

def strat_p(x, y, B=200000):
    ax = resolve(x); ay = resolve(y); Sobs = 0.0; tbls = []
    for name, mask in emasks:
        t = epoch_tbl(ax, ay, mask)
        if t is None: continue
        obs, null = t; var = null.var()
        if var <= 0: continue
        w = 1.0 / var; Sobs += w * obs; tbls.append((w, null))
    S = np.zeros(B)
    for w, null in tbls:
        S += w * null[rng.integers(0, len(null), size=B)]
    cnt = int((np.abs(S) >= abs(Sobs)).sum())
    return (1 + cnt) / (1 + B)

# ── семья D: глобальный masked циркул.сдвиг, MC, корреляция (как _gate_daily) ──
def global_p(x, y, B=3000):
    ax = resolve(x); ay = resolve(y); okx = ~np.isnan(ax)
    def mcorr(yv):
        ov = okx & ~np.isnan(yv)
        if ov.sum() < 30: return None
        xo = ax[ov]; yo = yv[ov]; xo = xo - xo.mean(); yo = yo - yo.mean()
        d = np.sqrt((xo * xo).sum() * (yo * yo).sum())
        return float((xo * yo).sum() / d) if d > 0 else 0.0
    obs = mcorr(ay); cnt = 0; tot = 0
    for _ in range(B):
        k = int(rng.integers(g, len(ay) - g))
        c = mcorr(np.roll(ay, k))
        if c is not None:
            tot += 1
            if abs(c) >= abs(obs): cnt += 1
    return (1 + cnt) / (1 + tot)

print('FLAGSHIP  (p_A стратиф. | p_D глобальный):', flush=True)
for x, y in [('sleep_deep','hrv'), ('sleep_total','hrv'), ('sleep_score','hrv')]:
    print('  %-16s p_A=%.2e  p_D=%.2e' % (x+'x'+y, strat_p(x,y), global_p(x,y)), flush=True)

# ── дискриминатор: эпоховые ступени co-двигаются, внутри эпох независимы ──
xs = np.full(N, np.nan); ys = np.full(N, np.nan)
for name, mask in emasks:
    sx = rng.normal(); idx = np.where(mask)[0]
    xs[idx] = sx + rng.normal(size=len(idx)); ys[idx] = sx + rng.normal(size=len(idx))
Rxs = grank(xs); Rys = grank(ys)
emx = [np.nanmean(xs[m]) for _, m in emasks]; emy = [np.nanmean(ys[m]) for _, m in emasks]
eta = np.corrcoef(emx, emy)[0, 1]
print('DISCRIMINATOR synthetic epoch-drift (eta=%.2f):' % eta, flush=True)
print('  p_A=%.2e (ждём НЕ значимо >0.05)  p_D=%.2e (ждём значимо)' % (strat_p(Rxs, Rys), global_p(Rxs, Rys)), flush=True)
print('DONE', flush=True)
