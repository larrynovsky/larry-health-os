#!/opt/homebrew/bin/python3.11
"""Аудит — только значимые варианты + реальные метрики."""
from health_db import get_conn

# Домены и целевые гены
DOMAINS = {
    "nutrition": {
        "func_genes": ["MTHFR","FUT2","VDR","BCMO1","APOE","TCF7L2","FADS1","FADS2","CYP1A2","ALDH2","SLC23A1","MTRR","MTR"],
        "prom_genes": ["MTHFR","FUT2","VDR","BCMO1","APOE","TCF7L2","FADS1","FADS2","CYP1A2","ALDH2"],
        "prom_topics": ["folate","vitamin","methylation","absorption","detox","nutrient","caffeine","alcohol","omega"],
    },
    "stress": {
        "func_genes": ["COMT","MAOA","SLC6A4","FKBP5","BDNF","NR3C1","CRHR1","HTR2A","OXTR","TPH1","TPH2","GABRA2","NTRK2"],
        "prom_genes": ["COMT","MAOA","SLC6A4","FKBP5","BDNF","OXTR","HTR2A"],
        "prom_topics": ["stress","dopamine","serotonin","cortisol","anxiety","mood","depression","empathy"],
    },
    "nervous_system": {
        "func_genes": ["APOE","BDNF","COMT","KIBRA","CACNA1C","TNF","IL6","SLC6A4","PSEN1","PSEN2","APP","SNCA","LRRK2"],
        "prom_genes": ["APOE","BDNF","KIBRA","CACNA1C","TNF","IL6"],
        "prom_topics": ["alzheimer","cognition","neuroprotection","brain","neurodegeneration","parkinson","dementia","ms"],
    },
    "movement": {
        "func_genes": ["ACTN3","ACE","PPARGC1A","IL6","ADRB2","AMPD1","COL5A1","COL1A1","IGF1","VEGFA","NOS3","HFE"],
        "prom_genes": ["ACTN3","ACE","PPARGC1A","IL6","ADRB2","AMPD1","COL5A1"],
        "prom_topics": ["muscle","endurance","recovery","injury","cardio","athletic","tendon","VO2","performance"],
    },
}

with get_conn() as c:
    # Что есть в daily_metrics
    cols = [r['name'] for r in c.execute("PRAGMA table_info(daily_metrics)").fetchall()]
    print("daily_metrics columns:", cols)

    for domain, cfg in DOMAINS.items():
        print(f"\n{'='*55}\nДОМЕН: {domain.upper()}")

        # Только значимые варианты (functional + pathogenic)
        gene_ph = ",".join("?" * len(cfg["func_genes"]))
        gv = c.execute(f"""
            SELECT rsid, gene, genotype, significance, annotation_source, clinical_summary
            FROM genetic_variants
            WHERE gene IN ({gene_ph})
              AND significance IN ('functional_variant','Pathogenic','Likely pathogenic',
                                   'association','risk_factor')
            ORDER BY annotation_source DESC, gene
        """, cfg["func_genes"]).fetchall()
        print(f"  Значимые genetic_variants: {len(gv)}")
        for r in gv:
            print(f"    {r['rsid']} {r['gene']} {r['genotype']} [{r['significance']} / {r['annotation_source']}]")
            print(f"      {r['clinical_summary'][:90]}")

        # Promethease
        g_ph = ",".join("?" * len(cfg["prom_genes"]))
        t_ph = ",".join("?" * len(cfg["prom_topics"]))
        prom = c.execute(f"""
            SELECT rsnum, geno, genes, magnitude, repute, genosummary, topic
            FROM promethease_variants
            WHERE (genes IN ({g_ph}) OR topic IN ({t_ph}))
              AND magnitude >= 1.5
            ORDER BY magnitude DESC LIMIT 15
        """, cfg["prom_genes"] + cfg["prom_topics"]).fetchall()
        print(f"  Promethease (mag≥1.5): {len(prom)}")
        for r in prom:
            print(f"    {r['rsnum']} {r['genes']} mag={r['magnitude']} [{r['repute']}] {r['geno']}: {r['genosummary'][:70]}")

        # Паттерны — ключевые слова специфичные для домена
        kws = {
            "nutrition": ["MTHFR","фолат","vitamin","B12","D3","omega","метилир","питан","caffeine"],
            "stress": ["COMT","serotonin","HRV","ВСР","readiness","кортизол","тревог","стресс","dopamin","OXTR"],
            "nervous_system": ["APOE","BDNF","когнитив","вагус","автоном","нейро","brain"],
            "movement": ["шаги","steps","тренир","кардио","ACTN3","ACE","нагрузк","восстановл","VO2","активн"],
        }
        kw_list = kws[domain]
        kw_cond = " OR ".join([f"description LIKE '%{k}%'" for k in kw_list])
        patterns = c.execute(f"""
            SELECT category, description, confidence, discovered
            FROM patterns
            WHERE {kw_cond}
            ORDER BY confidence DESC, discovered DESC LIMIT 8
        """).fetchall()
        print(f"  Паттерны: {len(patterns)}")
        for p in patterns:
            print(f"    [{p['confidence']}] {p['description'][:90]}")
