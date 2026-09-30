#!/opt/homebrew/bin/python3.11
"""Временный скрипт для аудита данных сна."""
from health_db import get_conn

with get_conn() as c:
    # --- genetic_variants ---
    rows = c.execute("""
        SELECT rsid, gene, genotype, significance, conditions, domain_tags, clinical_summary
        FROM genetic_variants
        WHERE domain_tags LIKE '%sleep%' OR domain_tags LIKE '%circadian%'
        ORDER BY gene, rsid
    """).fetchall()
    print(f"=== genetic_variants sleep/circadian: {len(rows)} ===")
    for r in rows:
        print(f"  {r['rsid']} {r['gene']} {r['genotype']} [{r['significance']}]")
        print(f"    {r['clinical_summary'][:100]}")

    # --- raw_snps структура ---
    cols = c.execute("PRAGMA table_info(raw_snps)").fetchall()
    print(f"\n=== raw_snps columns: {[r['name'] for r in cols]} ===")
    count = c.execute("SELECT COUNT(*) FROM raw_snps").fetchone()[0]
    print(f"  Total raw_snps: {count}")

    # --- patterns ---
    patterns = c.execute("""
        SELECT category, description, confidence
        FROM patterns
        WHERE category LIKE '%sleep%'
           OR description LIKE '%сон%'
           OR description LIKE '%sleep%'
           OR description LIKE '%deep%'
        ORDER BY confidence DESC LIMIT 15
    """).fetchall()
    print(f"\n=== patterns (sleep): {len(patterns)} ===")
    for p in patterns:
        print(f"  [{p['category']}] conf={p['confidence']} | {p['description'][:100]}")

    # --- agent_reports: предыдущий анализ конституции ---
    prev = c.execute("""
        SELECT date, substr(findings, 1, 500)
        FROM agent_reports
        WHERE agent_type='constitution'
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    if prev:
        print(f"\n=== Last constitution report ({prev['date']}) ===")
        print(prev[1])

    # --- Промпт из checkins про сон ---
    checkins = c.execute("""
        SELECT date, substr(text, 1, 200)
        FROM checkins
        WHERE text LIKE '%сон%' OR text LIKE '%sleep%'
        ORDER BY date DESC LIMIT 5
    """).fetchall()
    print(f"\n=== Recent checkins about sleep: {len(checkins)} ===")
    for ch in checkins:
        print(f"  {ch['date']}: {ch[1][:150]}")
