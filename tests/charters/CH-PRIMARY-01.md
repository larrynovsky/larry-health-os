# CH-PRIMARY-01 — Single-primary integrity (если БД повреждена)

**Linked UC:** UC-I-07.
**Запуск:** после инцидента в этой подсистеме (решение 2026-07-02: расписание не ведётся — ежедневное чтение утреннего отчёта покрывает CH-DAILY; остальные чартеры — процедура постмортема).
**Длительность:** 30-60 минут.

---

## Когда запускать

- БД на Studio показывает данные, которых не было.
- iCloud-копия БД отличается от Studio неожиданно.
- Smoke_tests упали с unexpected данными.
- Hostname guard поймал write с не-Studio (логи `health_db.log`).

---

## Маршрут диагностики

### Шаг 1: Проверить hostname-аудит

```bash
ssh <studio_ssh> "tail -100 ~/health_scripts/logs/triage.log | grep -i write"
```

Ищем строки с попытками write от не-Studio.

### Шаг 2: Сравнить БД

```bash
# На Studio
ssh <studio_ssh> "ls -la ~/health/data/health.db ~/health/data/health.db-wal"

# iCloud-копия (на MacBook)
ls -la "~/Library/Mobile Documents/com~apple~CloudDocs/health/data/health.db"
```

Если timestamps очень разные — split-brain возможен.

### Шаг 3: WAL checkpoint

```bash
ssh <studio_ssh> "/usr/bin/sqlite3 ~/health/data/health.db 'PRAGMA wal_checkpoint(TRUNCATE);'"
```

### Шаг 4: Сравнить ключевые таблицы

```bash
ssh <studio_ssh> "/usr/bin/sqlite3 ~/health/data/health.db 'SELECT COUNT(*) FROM daily_metrics;'"
```

Должно соответствовать ожидаемому размеру.

### Шаг 5: Откат через backup

Если несогласованность подтверждена:
- Найти последний здоровый backup в `~/health/backups/` **на Studio** (не iCloud — после 2026-05-09 backup-стратегия разделена по машинам, см. [docs/BACKUP_POLICY.md §R0](../../docs/BACKUP_POLICY.md)).
- Локально на Studio: `cp ~/health/backups/health_YYYY-MM-DD.db ~/health/data/health.db`.
- Перезапустить бот.
- ⚠️ Если на Studio backup'ы тоже повреждены — **восстанавливать неоткуда**: Time Machine на Studio не настроен (замер 2026-08-02, `tmutil destinationinfo` → «No destinations configured»). Канон и все 30 суток бэкапов на одном диске. См. [docs/BACKUP_POLICY.md §R2.5](../../docs/BACKUP_POLICY.md).

---

## Связано

- UC-I-07
- CLAUDE.md §8 (в закрытой части) — единственная canonical `health.db`, split-brain
- backup-стратегия (UC-I-05)
