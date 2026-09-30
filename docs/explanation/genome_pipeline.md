[English](genome_pipeline.en.md) · **Русский**

# Пайплайн геномных данных

> **Доменная разметка без API** (переехало из BLUEPRINT, раздел «Геномная подсистема», 2026-08-02):
> `genome_annotator.tag_domains_keywords()` — детерминированный keyword-маппинг
> варианта в домены здоровья, без обращения к внешним сервисам. Весовая сортировка
> по клинической значимости живёт в `genome_annotator.SEVERITY_PRIORITY`.

> **Тип документа:** Explanation (Diataxis).
> Для запуска обновления: `/genome_update` в Telegram.
> Схема таблиц: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## Источники данных

| Источник | Вариантов | Таблица | Скрипт |
|---|---|---|---|
| 23andMe TSV | сотни тысяч (зависит от чипа) | `raw_snps` | `genome_parser.py` |
| WGS VCF (любой провайдер) | миллионы позиций | `raw_snps` (merge) | `vcf_import_pipeline.py` |
| ClinVar/MyVariant | аннотации | `genetic_variants` | `genome_annotator.py` |

Итого в `raw_snps` после WGS-импорта: **сотни тысяч аннотированных SNP** (пересечение с ClinVar; точное число зависит от источников тенанта).

---

## Однократный WGS-импорт (vcf_import_pipeline.py)

Запускать один раз при получении нового WGS-файла.

```
Phase A — update_known (обновить известные позиции из WGS)
  raw_snps WHERE rsid IS NOT NULL → сопоставить с VCF по chr/pos/ref/alt
  → обновить genotype если WGS-колл надёжнее (GQ≥20, DP≥10)

Phase B — discover_new (найти новые ClinVar-варианты)
  Позиции WGS, которых нет в raw_snps → батчи в MyVariant.info
  → при клинической значимости → добавить в vcf_discovery → upsert в genetic_variants

Phase C — backfill_effect_alleles (заполнить effect_allele)
  Все genetic_variants без effect_allele → resolve_effect_allele()
  → batch POST myvariant.info → точечный UPDATE (не затрагивает significance)
  → гетерозиготные палиндромные автоматически разрешаются через HGVS/complement

Phase D — validation_report
  Concordance 23andMe vs WGS, coverage, конфликты → отчёт в /tmp/
```

Запуск на Studio (после rsync с MacBook):
```bash
/opt/homebrew/bin/python3.11 vcf_import_pipeline.py --vcf ~/health/data/*.vcf.gz
```

---

## Effect allele — статусы и резолюция

Каждый вариант в `genetic_variants` имеет `effect_allele_status`:

| Статус | Значение | effect_allele |
|---|---|---|
| `resolved` | однозначно определён (strand-aware) | установлен |
| `palindromic` | ref/alt взаимодополняют (C/G или A/T) — strand неразрешима | NULL |
| `palindromic_het_resolved` | гетерозигота + alt из HGVS/complement → carrier detection работает | установлен |
| `multiallelic_ambiguous` | >1 alt-аллель | NULL |
| `no_call` | генотип не два валидных нуклеотида (инделы, '--') | NULL |
| `no_data` | нет ref/alt в источниках | NULL |

### Carrier detection

```python
effect_allele in genotype and genotype not in ('--', 'II', 'DD')
```

### Палиндромная дыра и её закрытие

`effect_allele.py` намеренно возвращает `(None, 'palindromic')` для C/G и A/T SNP
— strand из одного генотипа неразрешима. Это правильно для гомозигот (CC, GG, TT, AA).

Для **гетерозиготных** палиндромных SNP (genotype=CG или AT) оба аллеля присутствуют
в строке генотипа, поэтому carrier detection сработает независимо от strand.

**Системная резолюция (backfill_effect_alleles.py v4, `compute_for_variant`):**

```
if status == "palindromic" and genotype in (AT, TA, CG, GC):
    Path 1: HGVS из clinical_summary  → c.187C>G → alt=G
    Path 2: complement(ref_allele)    → ref=T → alt=A
    → если нашли alt: status = palindromic_het_resolved
```

Все новые варианты из Phase B разрешаются автоматически при Phase C.
`backfill_effect_alleles.py` пропускает `palindromic_het_resolved` при повторном
запуске (WHERE guard), чтобы не перезаписывать.

**Исторический скрипт:** `fix_palindromic_het.py` — одноразовый бэкфилл,
выполненный 2026-06-26 для существующих вариантов до WGS-импорта. Оставлен как
документация алгоритма.

---

## Ежемесячное обновление (genome_update_agent.py)

Обновление проверяет, изменилась ли клиническая значимость уже известных вариантов
в базах данных ClinVar/MyVariant. Автоматического расписания нет — запускать руками
раз в месяц через `/genome_update` в Telegram.

```
run_monthly_update()
  1. Берёт все significant варианты из DB (limit 500)
  2. Батчи по 200 → POST myvariant.info/v1/variant
  3. Для каждого варианта: new_sig vs old_sig (поле significance)
  4. Обновляет genetic_variants если статус изменился
  5. _is_significant_change(): значимо только если риск ВЫРОС
  6. Если significant_changes > 0 → generate_genome_narrative() → Claude Sonnet
  7. save_genome_update_log() → genome_update_log; mark_genome_log_sent() после отправки
```

---

## Шкала тяжести и логика нарративов

```
Benign(0) → Likely benign(1) → Uncertain significance(2) →
risk factor/association(3) → Likely pathogenic(4) → Pathogenic(5)
```

**Нарратив генерируется только при движении ВВЕРХ по шкале.**
Снижение риска обновляется в БД, но пользователь уведомления не получает.

Нарратив объясняет: ген, что означает генотип, что изменилось, что делать.

---

## Почему нет автоматического расписания

Ежемесячный запуск вручную — намеренное решение:
- Обновления ClinVar редки и не требуют ежедневной проверки
- Нарратив требует верификации перед отправкой (клинически чувствительная информация)
- Rate-limited API: батч 500 вариантов занимает ~3 минуты
