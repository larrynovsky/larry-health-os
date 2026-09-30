[English](wave7_anti_recurrence_plan.en.md) · **Русский**

> Снято 2026-09-30: эксперимент отработал 15.06, служба, скрипт и шаблон удалены. Документ — история замысла.


# Wave 7 — Anti-recurrence (reason + decay + counter-evidence + UI)

Design-doc. После реализации → `docs/how-to/manage_recurrence.md`.

Зависимость: **Phase 3 должен быть закрыт** (нужен `dashboard_edits` из 3A.3 и status-action reject). Можно стартовать через 2-4 недели после Phase 3 — нужны реальные данные «как часто пользователь reject» для калибровки decay-timings.

## Что входит / что нет

Входит:
- Reason при reject (4 категории + free text)
- Decay-engine с per-reason стратегиями
- Counter-evidence escalation chip
- UI fold-down «недавно отвергнуто» на `/hypotheses`
- Manual unblock для permanent-reason

Не входит:
- Anti-recurrence для problems / protocols / experiments (только hypotheses в этой волне)
- Auto-escalation при N повторах («3-й раз = не шум») — оставлено как идея для Wave 8
- ML-обнаружение «семантически похожих» гипотез — только canonical-key по `metric_focus`

## Контекст из предыдущих волн

**Wave 5H-B (2026-05-14)** отключил `generate_hypothesis_from_drift` и `generate_hypothesis_from_correlation`. С тех пор гипотезы рождаются **только** через `monthly_consilium.py` 1-го числа в 04:00.

Это упрощает Wave 7: anti-recurrence нужен **в одной точке интеграции** — `monthly_consilium.py`. Не нужно патчить N писателей.

**Wave 5G-1** ввёл `metric_focus` normalize + group by canonical key. Это `_normalize_metric_focus()` — уже существует. Reuse без переписывания.

## Архитектурные решения

### Reason → decay-strategy mapping

Хранится в `system_config.rejection_decay_rules` (JSON):

```json
{
  "noise": {
    "strategy": "ttl",
    "days": 90
  },
  "superseded": {
    "strategy": "permanent"
  },
  "phase_mismatch": {
    "strategy": "while_period_active",
    "period_type_field": "period_type"
  },
  "methodological": {
    "strategy": "permanent_until_manual"
  }
}
```

Числа (90 дней для noise) — **с потолка**. Параметризованы в config, через 3 месяца после деплоя — пересчитать по реальным reject-data.

### canonical_key для metric_focus

Reuse `_normalize_metric_focus(payload)` из Wave 5G-1. Если он private — выделить как public helper в новом `recurrence_filter.py`.

Канонический ключ — это string-сериализация набора `(domain, primary_metric, intervention)` после normalization (lowercase, sorted, без duplicates). Точная форма определяется уже существующим кодом.

### Decay-engine: `recurrence_filter.py`

Один модуль, одна функция:

```python
def is_blocked(canonical_key: str, current_periods: list[dict], now: datetime) -> tuple[bool, str | None]:
    """Возвращает (blocked, reason) для решения 'создавать ли новую гипотезу с этим ключом'.
    
    Алгоритм:
    1. SELECT все отвергнутые гипотезы с этим canonical_key за всю историю
    2. Для каждой — взять payload.rejection_reason
    3. Применить decay-strategy из system_config
    4. Если хоть одна активная блокировка — return (True, reason)
    5. Иначе — return (False, None)
    """
```

Возвращает tuple, не bool — нужен reason для логирования.

### Counter-evidence

Если `is_blocked → False`, но в истории были `rejection` с тем же `canonical_key`:
- Новая гипотеза создаётся с `payload.recurrence_count = len(prior_rejects) + 1`
- `payload.previously_rejected_ids = [id1, id2, ...]`
- Chip `возвращается · N-й раз` на карточке

Это **не блокирует**, это **подсвечивает контекст**.

### Manual unblock

В UI fold-down «недавно отвергнуто» — рядом с reject-карточками, у которых `reason ∈ {superseded, methodological}` — кнопка «снять блок». Жмёт → запись `dashboard_edits.action='unblock_recurrence'` + `payload.rejection_unblocked_at=now`. `recurrence_filter` игнорирует rejects с unblocked_at IS NOT NULL.

## Этапы реализации

### W7-A: Reason при reject (~1.5 часа)

UI:
- На карточке гипотезы кнопка `reject` → не сразу выполняет, а раскрывает inline-form:
  - 4 radio: `шум` / `замещена` / `не подходит фазе` / `методологически неверно`
  - Опциональный textarea `детали`
  - Если `замещена` → select dropdown с активными гипотезами для `superseded_by`
  - Если `не подходит фазе` → select dropdown с активными `periods.type` для `period_type`
  - Кнопка `подтвердить отказ` отправляет POST

Endpoint:
```python
@app.post("/api/hypotheses/{id}/reject")
def hypothesis_reject(id: int, reason: str = Form(...), detail: str = Form(""),
                      superseded_by: int = Form(None), period_type: str = Form(None)):
    ...
    payload["resolution_type"] = "rejected"
    payload["rejection_reason"] = reason
    payload["rejection_reason_detail"] = detail
    if superseded_by: payload["superseded_by"] = superseded_by
    if period_type: payload["rejection_period_type"] = period_type
    ...
```

Confirm-button остаётся однокликом — там reason не нужен.

### W7-B: Decay-engine `recurrence_filter.py` (~2 часа)

Новый файл `~/health_scripts/recurrence_filter.py`:

```python
def get_decay_rules() -> dict:
    row = _q("SELECT value FROM system_config WHERE key='rejection_decay_rules'")
    return json.loads(row[0]["value"]) if row else DEFAULT_RULES

def is_blocked(canonical_key, current_period_types, now=None):
    now = now or datetime.now()
    rules = get_decay_rules()
    # SELECT отвергнутых
    rejects = _q("""SELECT id, value, updated_at FROM memory
                    WHERE category='hypothesis' AND active=0
                      AND value LIKE ? -- substring match canonical_key""", (f'%{canonical_key}%',))
    # фильтр по rejection_unblocked_at
    rejects = [r for r in rejects if not json.loads(r["value"]).get("rejection_unblocked_at")]
    
    for r in rejects:
        payload = json.loads(r["value"])
        reason = payload.get("rejection_reason", "noise")
        strategy = rules.get(reason, {}).get("strategy", "ttl")
        if strategy == "permanent" or strategy == "permanent_until_manual":
            return (True, reason)
        if strategy == "ttl":
            age_days = (now - datetime.fromisoformat(r["updated_at"])).days
            if age_days < rules[reason]["days"]:
                return (True, reason)
        if strategy == "while_period_active":
            blocked_period_type = payload.get("rejection_period_type")
            if blocked_period_type and blocked_period_type in current_period_types:
                return (True, reason)
    return (False, None)

def previous_reject_ids(canonical_key) -> list[int]:
    """Все id'шники прошлых reject для counter-evidence."""
    ...
```

Unit-tests на все 4 стратегии (W7-G).

### W7-C: Canonical-key extract (~1 час)

Найти существующую `_normalize_metric_focus` в `correlation_analysis.py` (Wave 5G-1). Вынести как public helper `canonical_metric_key(payload) -> str` в `recurrence_filter.py`.

Если функция уже public — просто import.

⚠️ **Проверить перед стартом**: `grep -n "normalize_metric_focus\\|canonical" ~/health_scripts/*.py`. Если канонизация делается inline в pipeline — extract.

### W7-D: Интеграция в `monthly_consilium.py` (~1.5 часа)

В `_build_consilium_input` или `_run_pass2_cbcr` (где гипотеза создаётся), **перед** INSERT:

```python
from recurrence_filter import is_blocked, previous_reject_ids, canonical_metric_key

for candidate in candidates:
    key = canonical_metric_key(candidate)
    current_period_types = [p["type"] for p in _q("SELECT type FROM periods WHERE active=1")]
    blocked, reason = is_blocked(key, current_period_types)
    
    if blocked:
        _log_edit("consilium", "0", "hypothesis_blocked_by_recurrence",
                  field=key, old_value=reason, new_value=None)
        # actor='consilium' через _log_edit signature расширить
        continue
    
    # Counter-evidence
    prior = previous_reject_ids(key)
    if prior:
        candidate["payload"]["recurrence_count"] = len(prior) + 1
        candidate["payload"]["previously_rejected_ids"] = prior
    
    # INSERT INTO memory ...
```

⚠️ **Перед стартом**: точно найти точку INSERT в текущем `monthly_consilium.py`. Возможно их больше одной (CBCR-pipeline сложный). `grep -n "INSERT INTO memory\\|INSERT INTO hypotheses_cbcr"`.

### W7-E: Counter-evidence chip в UI (~0.5 часа)

В `hypotheses.html`:

```html
{% if h.payload.recurrence_count and h.payload.recurrence_count > 1 %}
  <span class="chip chip--warning">возвращается · {{ h.payload.recurrence_count }}-й раз</span>
{% endif %}
```

В шаблоне отвергнутой карточки (внутри fold-down) — ссылка на этот repeated hypothesis для контекста.

### W7-F: UI fold-down «недавно отвергнуто» (~1 час)

В `hypotheses.html` после архив-секции:

```html
<details class="recently-rejected">
  <summary>недавно отвергнуто (30 дней) — {{ recently_rejected | length }}</summary>
  {% for r in recently_rejected %}
    <article class="card">
      <div class="card-head">
        <div class="card-meta">№ {{ r.id }} · {{ r.updated_at | age_days }}д · {{ r.payload.rejection_reason }}</div>
        <div class="card-chips">
          {% if r.payload.rejection_reason in ['superseded', 'methodological'] %}
            <button class="action-pill"
                    hx-post="/api/hypotheses/{{ r.id }}/unblock"
                    hx-confirm="Снять блок?"
                    hx-target="closest article"
                    hx-swap="outerHTML">снять блок</button>
          {% endif %}
        </div>
      </div>
      <h3 class="card-title">{{ r.payload.observation | short(140) }}</h3>
      {% if r.payload.rejection_reason_detail %}
        <p class="card-description">{{ r.payload.rejection_reason_detail }}</p>
      {% endif %}
    </article>
  {% endfor %}
</details>
```

В `dashboard.py` route `/hypotheses` — добавить query «отвергнутые за последние 30 дней».

CSS: `details summary { font-family: var(--font-serif); font-style: italic; color: var(--color-text-hint); cursor: pointer; }`.

### W7-G: Tests (~1.5 часа)

`tests/integration/test_recurrence_filter.py`:

```python
def test_noise_decay_90_days():
    """reject 89 дней назад с reason=noise → blocked. 91 день → unblocked."""

def test_superseded_permanent():
    """reject с reason=superseded → blocked всегда."""

def test_phase_mismatch_active():
    """reject с rejection_period_type='treatment' → blocked пока есть active period type=treatment.
       После end_date period — unblocked."""

def test_methodological_unblock():
    """reject methodological → blocked. После rejection_unblocked_at — unblocked."""

def test_counter_evidence():
    """reject → decay прошёл → recreate → recurrence_count=2, previously_rejected_ids=[prev_id]."""

def test_multiple_rejects_same_key():
    """3 reject разными reason → блокирует самый строгий."""
```

### W7-H: Документация (~0.5 часа)

- `docs/how-to/manage_recurrence.md` — как редактировать decay-rules, unblock manually
- `docs/explanation/anti_recurrence.md` — почему такая архитектура, какие альтернативы рассмотрены

## Оценка времени

Итого: **~9 часов**, можно за 2 сессии:
- Сессия 1 (5ч): W7-A + W7-B + W7-C + W7-G partial
- Сессия 2 (4ч): W7-D + W7-E + W7-F + W7-G complete + W7-H

## Definition of done

- [ ] При reject — выбор reason (4 категории + free text) обязателен
- [ ] `system_config.rejection_decay_rules` инициализирован defaults
- [ ] `recurrence_filter.is_blocked()` корректно работает по всем 4 стратегиям
- [ ] `canonical_metric_key()` детерминирован (одинаковый payload → одинаковый ключ)
- [ ] `monthly_consilium` фильтрует через `is_blocked` перед INSERT
- [ ] Counter-evidence: новая гипотеза с прошлыми reject получает `recurrence_count` + `previously_rejected_ids`
- [ ] UI fold-down «недавно отвергнуто (30 дней)» показывает reason chip
- [ ] Manual unblock работает для `superseded` и `methodological`
- [ ] 6+ tests PASS в `test_recurrence_filter.py`
- [ ] Документация: how-to + explanation files


## Scheduled data check 2026-06-15 (добавлено 2026-05-16)

Чтобы решение «нужен Wave 7 или нет» опиралось на реальные данные эксплуатации, а не на догадки — настроен автоматический сбор через launchd.

**Файлы:**
- `scripts/wave7_recurrence_check.py` — анализатор
- `launchd/com.larry.health.wave7-check.plist` — триггер 2026-06-15 09:00 (StartCalendarInterval с точной датой = один раз)

**Что соберёт скрипт:**
1. `dashboard_edits` за месяц: сколько reject, сколько confirm
2. `memory` за период: сколько новых гипотез создал `monthly_consilium` 1 июня
3. Грубое сравнение observations: есть ли дубли (reject ↔ new)
4. Записывает отчёт в `~/health/reports/wave7_data_2026-06-15.md`
5. Создаёт task в `/tasks` дашборда с текстом «Wave 7 review»

**Автоматическая рекомендация (REC-1..REC-4):**

| Условие | Рекомендация |
|---|---|
| <3 reject за месяц | REC-1: закрыть Wave 7 как надуманный |
| reject есть, дублей нет | REC-2: не нужен — `monthly_consilium` сам фильтрует через Wave 5G dedup |
| 1-2 дубля | REC-3: облегчённая версия (~3ч), 1 кнопка reject + 30-90д decay, без 4 категорий |
| 3+ дублей | REC-4: как описано в этом плане (~9ч) |

**Что не делает скрипт автоматически:**
- Не принимает решение — даёт рекомендацию, финал за пользователем
- Не правит ROADMAP — это последующий шаг после прочтения отчёта
- Не имплементирует Wave 7 — это отдельная сессия

**Проверка:**
```bash
ssh <studio_ssh> "launchctl list | grep wave7"
# Ожидание: -  0  com.larry.health.wave7-check
# Status=0 значит loaded и ждёт триггер
```

После 2026-06-15 09:00 — скрипт выполнится один раз, plist остаётся загруженным но не сработает повторно (дата конкретная).

**Failure modes:**
- Studio выключен в момент триггера → launchd запустит при следующем включении (стандартное поведение)
- Скрипт упадёт → лог в `~/health_scripts/logs/wave7_check.err.log`, task НЕ создастся
- БД locked другим агентом → busy_timeout=5000, обычно достаточно

## Открытые вопросы

1. **Decay-timings (90 дней для noise)** — с потолка. Через 3 месяца после деплоя — пересчитать по реальной частоте reject. Альтернатива: сделать timing configurable per-domain (sleep vs oncology могут требовать разных).

2. **Multiple active periods** — пользователь reject с `phase_mismatch period_type='travel'`, но также есть active `treatment`. Через 30 дней travel закончился, treatment продолжается. Гипотеза unblocked. Правильно ли это? Думаю да, потому что reject был привязан к **travel** specifically.

3. **`canonical_metric_key` устойчивость** — функция была написана для within-run dedup. Через 6 месяцев `payload` может иметь другую структуру (CBCR evolve). Стабильность ключа — открытый вопрос. Решение: сохранять `payload.canonical_key_v1` рядом, при INSERT — записывать. Тогда matching идёт по сохранённому ключу, не пересчёту.

4. **`actor` field в `dashboard_edits`** — план Phase 3 имеет `actor='dashboard'` default. Wave 7 расширяет значения до `consilium` / `correlation_analysis`. Нужна миграция или просто заполнение нового значения? Думаю просто заполнение — text field, без ограничений.

5. **Auto-escalation при N повторах** — `recurrence_count >= 3` означает «постоянно возвращается, может важно». Оставлено как идея для Wave 8. Сейчас — только chip-подсветка.

## Что может сломаться (lessons learned из Wave 5H)

В Wave 5H мы выключили auto-gen потому что гипотезы создавались шумными (600-607). Anti-recurrence нацелен закрыть **другой** класс шума: «то что уже отвергалось». Но есть риск **over-blocking** — гипотезу отвергли как `noise`, а через полгода у человека другая фаза и другая биохимия — та же канон-key, блок ещё активен.

Защита от over-blocking:
1. **Phase_mismatch reason** — переход между фазами **сразу снимает блок**. Это работает только если пользователь правильно выбирает reason. Если поставил `noise` — блок 90 дней независимо от фазы.
2. **Manual unblock** — для `superseded` и `methodological`. Если пользователь видит «надо пересмотреть» — снимает блок.
3. **Counter-evidence chip** — если гипотеза прорвалась через decay и оказалась `recurrence_count=2`, это **сигнал**. Не блок, а напоминание.

Это компромисс, не silver bullet.
