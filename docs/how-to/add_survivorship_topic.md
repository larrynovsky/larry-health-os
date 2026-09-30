[English](add_survivorship_topic.en.md) · **Русский**

# Как добавить новую тему для литературного агента

> **Тип:** How-to (Diataxis).
> Архитектура: `survivorship_engine.md` §«Литературная цепочка».
> Конфиг: `~/health/data/survivorship_topics.yaml`.

## Когда использовать

Когда литература затронула новую область — например, появилось активное лечение, или специалист порекомендовал отслеживать конкретный класс препаратов.

## Шаги

### 1. Открыть `~/health/data/survivorship_topics.yaml` и добавить блок

```yaml
- id: new_topic_id
  name: "Описание темы"
  pubmed_query: 'термины AND ("survivor*" OR "follow-up")'
  domain: drug_class_X | autonomic_sleep_fatigue | metabolic_hepatic | ...
  relevance_window_years: 3
  priority: high | medium | low
```

### 2. Тестовый прогон через PubMed E-utilities

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -c '
import pubmed_client
res = pubmed_client.search_pubmed(\"твой запрос\", max_results=5, years_back=3)
for r in res: print(r[\"pmid\"], r[\"title\"][:80])
'"
```

**Если 0 результатов** → запрос слишком узкий. Расширь термины через OR.
**Если >50 результатов** → слишком широкий. Добавь survivor*/follow-up/post-treatment.
**Оптимально 5–20 за год.**

### 3. Дождаться воскресенья 04:00

Cron `com.larry.health.literature-search` подхватит новую тему. В понедельник утром в `agent_reports` появится `literature_search` с твоими candidates.

### 4. Альтернатива — manual smoke

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/pubmed_searcher.py"
```

## Тематические домены

| Домен | Пример темы |
|---|---|
| `autonomic_sleep_fatigue` | CRF, CBT-I, autonomic dysfunction, mind-body |
| `metabolic_hepatic` | cardio-oncology, glucose, DILI recovery |
| `nutrition_post_surgery` | nutrition after cancer surgery, sarcopenia |
| `drug_class_anthracycline` | long-term cardiac follow-up |
| `drug_class_aromatase_inhibitor` | bone health, arthralgia |
| `anatomy_post_surgery` | lymphedema, mobility rehab |

Это примеры: домены конкретной установки лежат в её `survivorship_topics.yaml` и отражают её
собственные темы.

## Антипаттерны

- **PubMed-запрос с PII** (имя + диагноз пациента) — запросы попадают в публичный лог E-utilities. Всегда topic-based, never patient-based.
- **Слишком общий запрос** (например `cancer AND survivor`) — найдёт 1000+ статей, cheap-triage потом потратит много токенов.
- **Дубль с существующей темой** — проверь, что нет похожей в `survivorship_topics.yaml`.
