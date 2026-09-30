<!-- translation-of: docs/how-to/add_survivorship_topic.md sha256:54d9557995d4 -->
**English** · [Русский](add_survivorship_topic.md)

# How to add a new topic for the literature agent

> **Type:** How-to (Diataxis).
> Architecture: `survivorship_engine.md` §“Literature pipeline”.
> Config: `~/health/data/survivorship_topics.yaml`.

## When to use

When the literature covers a new area — for example, active treatment has started, or a specialist has recommended tracking a specific drug class.

## Steps

### 1. Open `~/health/data/survivorship_topics.yaml` and add a block

```yaml
- id: new_topic_id
  name: "Topic description"
  pubmed_query: 'terms AND ("survivor*" OR "follow-up")'
  domain: drug_class_X | autonomic_sleep_fatigue | metabolic_hepatic | ...
  relevance_window_years: 3
  priority: high | medium | low
```

### 2. Run a trial through PubMed E-utilities

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -c '
import pubmed_client
res = pubmed_client.search_pubmed(\"your query\", max_results=5, years_back=3)
for r in res: print(r[\"pmid\"], r[\"title\"][:80])
'"
```

**If there are 0 results** → the query is too narrow. Broaden the terms with OR.
**If there are >50 results** → it is too broad. Add survivor*/follow-up/post-treatment.
**The target is 5–20 per year.**

### 3. Wait until Sunday at 04:00

The cron job `com.larry.health.literature-search` will pick up the new topic. On Monday morning, `agent_reports` will contain `literature_search` with your candidates.

### 4. Alternative — a manual smoke check

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/pubmed_searcher.py"
```

## Topic domains

| Domain | Example topic |
|---|---|
| `autonomic_sleep_fatigue` | CRF, CBT-I, autonomic dysfunction, mind-body |
| `metabolic_hepatic` | cardio-oncology, glucose, DILI recovery |
| `nutrition_post_surgery` | nutrition after cancer surgery, sarcopenia |
| `drug_class_anthracycline` | long-term cardiac follow-up |
| `drug_class_aromatase_inhibitor` | bone health, arthralgia |
| `anatomy_post_surgery` | lymphedema, mobility rehab |

These are examples: a given install's domains live in its `survivorship_topics.yaml` and reflect
its own topics.

## Antipatterns

- **A PubMed query with PII** (the patient's name + diagnosis) — queries go into the public E-utilities log. Always topic-based, never patient-based.
- **An overly broad query** (for example, `cancer AND survivor`) — it will find 1000+ papers, and cheap-triage will then consume many tokens.
- **A duplicate of an existing topic** — check that there is no similar one in `survivorship_topics.yaml`.
