[English](norm_documents.en.md) · **Русский**

# Справочник: реестр документов нормы и снимки (`norm_documents`)

*Тип документа: справочник. Нить `norm-from-documents`, 2026-09-02. Объяснение — `docs/explanation/norm_kinds.md`; карта источников — `docs/reference/norm_sources.md`.*

---

## Реестр `norm_documents` (таблица) и `norm_documents.DOCUMENTS` (адреса)

| Колонка | Смысл |
|---|---|
| `id` | `CTCAE_v6.0` (**текущий**, `norm_documents.CTCAE_CURRENT`), `CTCAE_v5.0` (предыдущий, для дифа), `EFLM_BV`; документы тенанта (гайдлайны наблюдения по его эпизодам, решения его врача) — в `data/norm_docs/documents_tenant.json` (приватная зона), дописываются к реестру при импорте |
| `version`, `issued`, `url` | версия, дата выпуска, где лежит |
| `local` | локальная копия в `data/norm_docs/` (для CTCAE xlsx и снимка EFLM), `sha256` — её checksum |
| `kind` | какой вид нормы даёт: `decision_threshold` · `personal` (RCV) · `schedule` (каданс наблюдения) |
| `applies_to` | `oncology` · `*` · класс эпизода тенанта (из `schedules.json`) — сопоставляется с эпизодами (`episodes_of_care`) |
| `next_check_days`, `next_check`, `last_check`, `remote_state` | каденция проверки версии; датчик `check_norm_documents_fresh` |

## Снимки в репо

`data/norm_docs/ctcae_v6.0_2026-01-26.xlsx` (NCIt-экспорт, длинная раскладка: строка на «Grade N Term») и `ctcae_v5.0_2017-11-27.xlsx` («Clean Copy», широкая) — документы; `norm_documents._wide_table` сводит обе раскладки в одну. `ctcae_lab_terms_v6.json` / `ctcae_lab_terms.json` — карты термов по версиям (данные): терм → канон, направление, семейство (`multiple` — кратные ULN/LLN; `absolute` — числа в `unit_pick`, `scale` в единицы канона). `ctcae_lab_v6.0.json` — **текущий снимок**, его читают сид `health_db._seed_safety_lab_thresholds` и резерв `safety_net._FALLBACK_LAB`; `ctcae_lab_v5.0.json` — предыдущий, для `diff_ctcae`. Смена версии: новый файл + карта + запись в `CTCAE_FILES`/`DOCUMENTS` + `CTCAE_CURRENT` + оба снимка + диф в коммите; строки прежней версии сид уводит в `variant='superseded:<id>'`, `active=0`.

Диф v5→v6 (2026-09-02): нейтрофилы — все грейды на ступень ниже (urgent 1.5→1.0, critical 1.0→0.5 ×10³/µL, warn стал абсолютным 1.5); тромбоциты — терм `Thrombocytopenia`, G3 <50–10; липаза critical 2→3×ULN; гипергликемия стала числовой (натощак: warn >ULN, urgent >160, critical >250 mg/dL); натрий urgent 129→130; ALP (одна ступень «>Baseline and ULN»), CPK (терм исчез) и лимфопения («Present») из карты сняты — ALP и лимфоциты судятся видом 1. `eflm_bv.json` — снимок API: все измеранды EFLM с мета-оценкой CV_I, чьё display_name словарь канона (`lab_canon`) сводит к каноническому имени (правило вместо ручной карты `eflm_terms.json`, решение владельца 2026-09-25; британские написания — `lab_canon._REFERENCE_SPELLINGS`; два измеранда в один канон — не берётся ни один, предупреждение) (`cvi_median/lower/upper/n`, `updated_at`).

## Правила вывода из CTCAE

grade 1 → `warn`, grade 2 → `urgent`, grade 3 → `critical`; grade 4 не сеется. Первый числовой токен грейда — граница; `LLN`/`ULN` → `kind='relative', value=1.0`. Симптом-зависимый грейд с тем же числом (Hypokalemia G2) — не порог. Ветка «if baseline was abnormal» не поддержана (берётся «baseline normal»). Одна система единиц — conventional, как на бланке. Не из CTCAE: Amylase (личный порог владельца), онкомаркеры (их в CTCAE нет); в v6 — ещё ALP, CPK, лимфоциты (см. диф).

## RCV

`rcv_pct(metric, cva_pct=None)` = 1.65·√2·√(CV_A² + CV_I²), CV_I — медиана EFLM; CV_A по умолчанию 0.5·CV_I (желательная APS). Ключ `system_config` `norm.cva_source` — задел под CV_A лаборатории. На 2026-09-02: CEA 17.8 %, CA19-9 11.3 %, HGB 7.0 %, MCV 2.0 %, Albumin 6.5 %.

## Правило подтверждения роста (`schedules.json::rules`, EGTM 2014)

`norm_documents.confirmation_metrics()` читает `data/norm_docs/schedules.json::rules[id=confirm_rise_before_action]` — список канон-имён (CEA, CA19-9) с дословной цитатой источника ([DOI 10.1002/ijc.28384](https://doi.org/10.1002/ijc.28384)): *«Any increase in levels must be confirmed with a second sample prior to undertaking further investigations»*. Читатель — `safety_net.check_lab_trends`: превышение RCV последней парой даёт уровень порога (urgent) только если и предыдущая, и последняя точки превышают порог относительно точки перед ними (рост держится на двух заборах); иначе — warn с текстом «подтвердить повторным». Метрики вне списка судятся по одной паре. Правило не читается → судим по одной паре и шлём fallback-алерт (`lab_trend_confirm_rule`). Почему так, а не пол по величине: `docs/explanation/norm_kinds.md`.

## Пол по величине для RCV-тренда (`lab_trend_thresholds.near_boundary_share`)

Слово владельца 2026-09-03, не документ: `near_boundary_share=0.2`, `near_boundary_source='owner_word:2026-09-03'`
(сид `health_db._seed_lab_trend_thresholds` заполняет только NULL; резерв `health_db.LAB_TREND_NEAR_BOUNDARY_SHARE`).
`safety_net.check_lab_trends` судит RCV, только если последняя точка ≥ (1 − share)·`ref_high` её бланка — и только
при `ref_low` 0/пусто (онкомаркеры); двусторонние интервалы и точки без референса — как прежде. Записать другое число
или снять пол для аналита: `UPDATE lab_trend_thresholds SET near_boundary_share=?, near_boundary_source='owner_word:<дата>' WHERE metric=?`.

## Референс вида 1 — бланк, не справочник (`lab_refs`)

Документ вида 1 — сам бланк лаборатории (`lab_results.ref_low/ref_high`), референс принадлежит методу. `system_config.lab_refs` — **кэш**, не сид: `health_db._refresh_lab_refs()` на каждом `init_db` (после миграций `lab_results`) пишет `labs_db.compute_bank_refs(min_docs)` — моду напечатанного интервала по ≥`norm.witness_min_docs` разным документам, формат `{canon: [lo, hi, unit, n_docs]}`, `source='bank_modal'`; `lab_refs_meta` — дата и n. `get_lab_refs()` отдаёт `(lo, hi, unit)` или `{}`; литерала-резерва нет. Порядок у читателей: референс строки → мода → «референс не установлен». `check_threshold_source_is_document` краснеет на записи кэша с иным источником или n_docs < min_docs. Ограничение: мода — интервал самой частой лаборатории; при многих лабораториях это большинство, не истина.

## Обновление

`python3 -c "import norm_documents as nd; nd.snapshot_ctcae(); nd.fetch_eflm()"` на машине с сетью → диф снимков в коммите → `init_db` на Studio перепишет строки (`INSERT OR IGNORE` + UPDATE трендов). Coherence: `test_snapshot_equals_import_of_document`, `test_fallback_lab_equals_seed`.
