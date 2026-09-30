#!/opt/homebrew/bin/python3.11
"""Аудит данных по всем 4 оставшимся доменам."""
from health_db import get_conn

DOMAINS = {
    "nutrition": {
        "tags": ["nutrition", "vitamin", "folate", "methylation", "detox", "metabol"],
        "genes": ["MTHFR","FUT2","VDR","BCMO1","APOE","TCF7L2","FADS1","FADS2","SLC23A1","ALDH2","CYP1A2"],
        "topics_prom": ["folate","vitamin","methylation","absorption","detox","nutrient","caffeine","alcohol"],
        "metric_cols": [],
        "patterns_kw": ["MTHFR","фолат","витамин","питан","nutrition","vitamin","folate","B12","D3","omega"],
    },
    "stress": {
        "tags": ["stress","neurology","dopamine","serotonin","cortisol","autonomic"],
        "genes": ["COMT","MAOA","SLC6A4","FKBP5","BDNF","NR3C1","CRHR1","HTR2A","OXTR","TPH1","TPH2"],
        "topics_prom": ["stress","dopamine","serotonin","cortisol","anxiety","mood","depression"],
        "metric_cols": ["hrv","readiness"],
        "patterns_kw": ["стресс","HRV","ВСР","readiness","cortisol","COMT","serotonin","dopamin","тревог","стресс"],
    },
    "nervous_system": {
        "tags": ["neurology","cognitive","neuroprotection","alzheimer","parkinson","brain"],
        "genes": ["APOE","BDNF","COMT","DTNBP1","NRG1","KIBRA","CACNA1C","TNF","IL6","SLC6A4"],
        "topics_prom": ["alzheimer","cognition","neuroprotection","brain","neurodegeneration","parkinson","ms"],
        "metric_cols": ["hrv"],
        "patterns_kw": ["когнитив","memoria","brain","нервн","APOE","BDNF","вагус","автоном"],
    },
    "movement": {
        "tags": ["musculoskeletal","cardiology","sports","injury","recovery","muscle"],
        "genes": ["ACTN3","ACE","PPARGC1A","IL6","ADRB2","MCT1","AMPD1","COL5A1","COL1A1","MMP3","IGF1"],
        "topics_prom": ["muscle","endurance","recovery","injury","cardio","athletic","tendon","bone"],
        "metric_cols": ["steps","active_calories","rhr","vo2max"],
        "patterns_kw": ["шаги","steps","активност","movement","кардио","ЧСС","rhr","тренир","восстановл","нагрузк"],
    },
}

with get_conn() as c:
    for domain, cfg in DOMAINS.items():
        print(f"\n{'='*60}")
        print(f"ДОМЕН: {domain.upper()}")

        # genetic_variants
        tag_conditions = " OR ".join([f"domain_tags LIKE '%{t}%'" for t in cfg["tags"]])
        gv = c.execute(f"""
            SELECT rsid, gene, genotype, significance, clinical_summary
            FROM genetic_variants
            WHERE {tag_conditions}
            ORDER BY significance, gene
        """).fetchall()
        print(f"  genetic_variants: {len(gv)}")
        for r in gv[:8]:
            print(f"    {r['rsid']} {r['gene']} {r['genotype']} [{r['significance'][:20]}]: {r['clinical_summary'][:60]}")
        if len(gv) > 8:
            print(f"    ... и ещё {len(gv)-8}")

        # promethease
        gene_ph = ",".join("?" * len(cfg["genes"]))
        topic_ph = ",".join("?" * len(cfg["topics_prom"]))
        prom = c.execute(f"""
            SELECT rsnum, genes, magnitude, repute, genosummary
            FROM promethease_variants
            WHERE (genes IN ({gene_ph}) OR topic IN ({topic_ph}))
              AND magnitude >= 1.5
            ORDER BY magnitude DESC LIMIT 10
        """, cfg["genes"] + cfg["topics_prom"]).fetchall()
        print(f"  promethease (mag≥1.5): {len(prom)}")
        for r in prom[:5]:
            print(f"    {r['rsnum']} ({r['genes']}) mag={r['magnitude']} [{r['repute']}]: {r['genosummary'][:60]}")

        # patterns
        kw_conditions = " OR ".join([f"description LIKE '%{k}%'" for k in cfg["patterns_kw"]])
        patterns = c.execute(f"""
            SELECT category, description, confidence
            FROM patterns
            WHERE category LIKE '%{cfg["tags"][0]}%'
               OR {kw_conditions}
            ORDER BY confidence DESC LIMIT 5
        """).fetchall()
        print(f"  patterns: {len(patterns)}")
        for p in patterns:
            print(f"    [{p['confidence']}] {p['description'][:80]}")

        # metrics
        if cfg["metric_cols"]:
            cols = ", ".join([f"ROUND(AVG({col}),2) as {col}" for col in cfg["metric_cols"] if col not in ["steps","active_calories","vo2max"]])
            if cols:
                row = c.execute(f"SELECT {cols} FROM daily_metrics WHERE date >= '2025-01-01'").fetchone()
                print(f"  metrics 2025+: {dict(row)}")
