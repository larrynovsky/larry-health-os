import time, numpy as np
from fdr_harness import run_cell, wall, H

t0 = time.time()
TR = 300          # trials per cell (illustrative; doc specifies 50k for release)
TR_BIG = 150      # fewer for T=3650 (heavier)

def line(tag, V, T, phi, q, eng, res, ntrue):
    m = V*(V-1)//2
    b, y = res['BH'], res['BY']
    print(f"{tag:22} | m={m:3d} T={T:4d} phi={phi:.1f} q={q:.2f} {eng:6} | "
          f"BH pow={b['power']:.2f} fdr={b['fdr']:.3f} | "
          f"BY pow={y['power']:.2f} fdr={y['fdr']:.3f}")

print("="*118)
print("GROUP 1 — resolution & discreteness: sparse alternatives; parameters shown per cell")
print(f"   resolution wall (lone signal declarable iff T>=): BH>={wall(45,0.10,'BH')}  BY>={wall(45,0.10,'BY')}")
print("-"*118)
for T in [90, 365, 3650]:
    tr = TR_BIG if T == 3650 else TR
    for eng in ['oracle', 'shift']:
        r = run_cell(V=10, T=T, phi=0.2, n_true=3, rho=0.5, q=0.10, engine=eng, trials=tr, seed=101)
        line("G1 sparse", 10, T, 0.2, 0.10, eng, r, 3)

print("="*118)
print("GROUP 2 — q effect and BH vs BY on the SHIFT engine; parameters shown per cell")
print("-"*118)
for q in [0.10, 0.05]:
    r = run_cell(V=10, T=365, phi=0.2, n_true=3, rho=0.5, q=q, engine='shift', trials=TR, seed=202)
    line("G2 shift", 10, 365, 0.2, q, 'shift', r, 3)
    ro = run_cell(V=10, T=365, phi=0.2, n_true=3, rho=0.5, q=q, engine='oracle', trials=TR, seed=202)
    line("G2 oracle", 10, 365, 0.2, q, 'oracle', ro, 3)

print("="*118)
print("GROUP 3 — family size m and the wall T>=m*H_m/q; parameters shown per cell")
print("-"*118)
for V in [7, 10, 15]:
    m = V*(V-1)//2
    print(f"   m={m:3d}: wall BH>={wall(m,0.10,'BH')}  BY>={wall(m,0.10,'BY')}   (T=365)")
    for eng in ['oracle', 'shift']:
        r = run_cell(V=V, T=365, phi=0.2, n_true=3, rho=0.5, q=0.10, engine=eng, trials=TR, seed=303)
        line("G3", V, 365, 0.2, 0.10, eng, r, 3)

print("="*118)
print("GROUP 4 — autocorrelation stress on the ORACLE engine: sparse, T=365, q=0.10, m=45")
print("-"*118)
for phi in [0.2, 0.7]:
    r = run_cell(V=10, T=365, phi=phi, n_true=3, rho=0.5, q=0.10, engine='oracle', trials=TR, seed=404)
    line("G4 oracle", 10, 365, phi, 0.10, 'oracle', r, 3)

print("="*118)
print(f"done in {time.time()-t0:.1f}s   (TR={TR}, MC-SE on FDR ~ {np.sqrt(0.1*0.9/TR):.3f})")
