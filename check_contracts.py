#!/usr/bin/env python3.11
"""
check_contracts.py — проверка целостности межмодульных связей.

Запускай перед любым изменением кода:
    python3.11 check_contracts.py

Проверяет:
1. Все ключевые модули импортируются без ошибок
2. Публичные функции контрактов присутствуют и вызываемы
3. Критические пути к файлам существуют
4. Нет циклических импортов в ключевых модулях
5. Прямые нарушения (sqlite3 минуя health_db) — выводит предупреждения
"""

import infra_config
import sys
import importlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

errors = []
warnings = []
passed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed
    if condition:
        print(f"  {PASS} {label}")
        passed += 1
    else:
        print(f"  {FAIL} {label}" + (f": {detail}" if detail else ""))
        errors.append(label)


def warn(label: str, detail: str = ""):
    print(f"  {WARN} {label}" + (f": {detail}" if detail else ""))
    warnings.append(label)


# ── 1. Импортируемость модулей ─────────────────────────────────────────────
print("\n[1] Импортируемость модулей")
MODULES = [
    "health_db", "health_ai", "hai_core", "gp_agent", "checkin_agent",
    "task_agent", "genome_context", "calendar_client",
    "safety_net",
    "lifestyle_agents", "pubmed_client",
    "epistemic_skill.loader",
]
for mod_name in MODULES:
    try:
        mod = importlib.import_module(mod_name)
        check(f"import {mod_name}", True)
    except Exception as e:
        check(f"import {mod_name}", False, str(e)[:80])


# ── 2. Контракты публичных функций ─────────────────────────────────────────
print("\n[2] Публичные функции контрактов")

# Контракт модуля = сайдкар contracts/<путь-модуля>.json, а не строка в общем словаре.
# Зачем так: новый модуль приносит СВОЙ файл и не правит общий — в мультисессии общий файл это
# shared state (CLAUDE.md §10), а словарь на 260 модулей вырос бы в God-файл, который сам
# невозможно переписать. Формат JSON, не YAML: тот же сайдкар читает движок (dispgate), а движок
# сознательно без сторонних зависимостей.
# Ридер ОДИН и живёт в движке (indexer.contract_sidecars) — здесь только адаптация под
# {имя-импорта: [функции]}. Свой ридер здесь был бы вторым домом формата, то есть дублем.
# Полноту поверхности НОВЫХ модулей стережёт dispgate (K1); тут — только существование/вызываемость.
from project_context import indexer as _pcix

_sidecars, _bad_sidecars = _pcix.contract_sidecars(str(ROOT))
CONTRACTS = {k.replace("/", "."): v["public"] for k, v in _sidecars.items()}
check("contracts/: сайдкары загружены", bool(CONTRACTS),
      "каталог contracts/ пуст или отсутствует — контракты НЕ проверены")
for _b in _bad_sidecars:                      # битый json ≠ «контракта нет»: громко, не тихо
    check(f"contracts/{_b.split(':')[0]}: читается", False, _b)

for mod_name, funcs in CONTRACTS.items():
    try:
        mod = importlib.import_module(mod_name)
        for fn in funcs:
            has = hasattr(mod, fn) and callable(getattr(mod, fn))
            check(f"{mod_name}.{fn}()", has)
    except Exception:
        for fn in funcs:
            check(f"{mod_name}.{fn}()", False, "module failed to import")


# ── 3. Критические пути к файлам ──────────────────────────────────────────
print("\n[3] Критические пути")
SECRETS = Path.home() / ".health_secrets"

# Секреты — на обеих машинах; отчёты — в iCloud doc-store (health/reports).
PATHS = {
    "anthropic_key": SECRETS / "anthropic_key",
    "oura_token": SECRETS / "oura_token",
    "telegram_token": SECRETS / "telegram_token",
    "reports/": infra_config.cloud_dir("reports"),
    "epistemic_skill/discipline.txt": ROOT / "epistemic_skill" / "discipline.txt",
    "epistemic_skill/coordinator.txt": ROOT / "epistemic_skill" / "coordinator.txt",
}
# БД и метрики — single-source на Studio (R1/R2, split-brain fix 2026-06-18).
# iCloud-копия БД удалена; на не-Studio хосте их нет by design → проверяем на Studio.
import socket as _sock
import health_db as _hdb
if _hdb._infra.is_primary():
    PATHS["health.db"] = _hdb.DB_PATH
    PATHS["daily_metrics/"] = _hdb.METRICS_DIR
for label, path in PATHS.items():
    check(str(label), path.exists(), str(path))


# ── 4. LaunchAgents зарегистрированы ──────────────────────────────────────
# Архитектура: MacBook запускает только backup.
# Все остальные агенты работают на Mac Studio (адрес — infra_config.STUDIO_SSH).
print("\n[4] LaunchAgents")

# MacBook: только backup
MACBOOK_AGENTS = ["com.larry.health.backup"]
local_list = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
for agent in MACBOOK_AGENTS:
    check(f"{agent} (MacBook)", agent in local_list.stdout)

# Studio: все production-агенты — проверяем через SSH
STUDIO_AGENTS = [
    "com.larry.health.bot",
    "com.larry.healthbot.morningwake",
    # com.larry.healthbot.checkinwake снят 2026-08-17 вместе с автозапуском чекина
    # (агент будил Mac в 18:45 ровно под него; плист уехал в _retired_backups/).
    # com.larry.health.daily снят 2026-09-26 (iCloud-путь Apple Health пуст с REST 06.07;
    # плист в ~/Library/LaunchAgents/_retired/, шаблон удалён).
    "com.larry.health.reminders-sync",
    "com.larry.health.watcher",
]
studio_result = subprocess.run(
    ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
     infra_config.STUDIO_SSH, "launchctl list"],
    capture_output=True, text=True
)
if studio_result.returncode == 0:
    for agent in STUDIO_AGENTS:
        check(f"{agent} (Studio)", agent in studio_result.stdout)
else:
    warn("Studio SSH недоступен", "LaunchAgents на Studio не проверены")


# ── 5. Нарушения контрактов (tech debt) ───────────────────────────────────
print("\n[5] Нарушения контрактов (tech debt — только предупреждения)")
TD_VIOLATIONS = [
    ("gp_agent.py",          "sqlite3.connect",   "TD-02: прямой DB минуя health_db"),
    ("health_ai.py",         "import sqlite3 as _sq", "TD-03: прямой DB минуя health_db"),
    ("constitution_analysis.py", "sqlite3.connect", "TD-04: DB_PATH хардкод"),
    ("genome_parser.py",     "sqlite3.connect",   "TD-05: прямой sqlite3"),
]
for filename, pattern, message in TD_VIOLATIONS:
    filepath = ROOT / filename
    if filepath.exists():
        content = filepath.read_text()
        if pattern in content:
            warn(f"{filename}", message)


# ── 6. Ратчет: публичная поверхность доменных модулей = ре-экспорт health_db ──
# Контракт каждого <domain>_db.py = его ре-экспорт `from <domain>_db import (...)`
# в health_db. Необъявленная публичная функция = протечка → ОШИБКА (блокирует commit).
# Baseline 2026-06-28 после рефакторинга C: 0 нарушений. Держим 0: новую публичную
# функцию домена надо либо ре-экспортнуть (= объявить контрактом), либо сделать _private.
print("\n[6] Ратчет публичной поверхности доменных модулей")
import ast as _ast
_hdb_text = (ROOT / "health_db.py").read_text(encoding="utf-8")
_reexports: dict[str, set] = {}
for _node in _ast.parse(_hdb_text).body:
    if isinstance(_node, _ast.ImportFrom) and _node.module and _node.module.endswith("_db"):
        _reexports.setdefault(_node.module, set()).update(a.name for a in _node.names)
for _mod, _declared in sorted(_reexports.items()):
    _mf = ROOT / f"{_mod}.py"
    if not _mf.exists():
        check(f"{_mod}: файл существует", False, "ре-экспорт есть, файла нет")
        continue
    _pub = {n.name for n in _ast.parse(_mf.read_text(encoding="utf-8")).body
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
            and not n.name.startswith("_")}
    _undeclared = sorted(_pub - _declared)
    check(f"{_mod}: нет необъявленных публичных", not _undeclared,
          f"публичные не в ре-экспорте: {_undeclared}" if _undeclared else "")


# ── Telegram-секреты per-tenant (2026-07-01, инцидент кросс-тенант утечки) ──
# Читатель telegram_token/chat_id ДОЛЖЕН быть env-aware (HEALTH_SECRETS_DIR), иначе
# алёрт тенанта уходит владельцу (баг: гипотезы партнёра ушли владельцу). Whitelist:
# канонические резолверы + admin-global (алерты инфры → всегда владельцу) + doc-генераторы.
from secrets_paths import (ADMIN_GLOBAL_TG_ALLOW, GUARDED_SUFFIXES, SECRET_SCOPE,
                           referenced_secret_names, referenced_via_variable)
# Единый источник admin-global отправителей — secrets_paths.ADMIN_GLOBAL_TG_ALLOW
# (тот же список читает датчик полноты доставки). Здесь + канонические резолверы и
# doc-генераторы: легитимно упоминают .health_secrets, но не отправители-утечки.
_TG_ALLOW = ADMIN_GLOBAL_TG_ALLOW | {
    "notify.py", "bot/filters.py", "check_contracts.py",
    "gen_arch_blocks.py", "gen_key_paths.py",
}
# ПЕРИМЕТР — ИЗ ОДНОГО ДОМА (2026-09-02): было rglob("*.py"), и канал на shell
# (watch_and_test.sh, icloud_conflict_check.sh) сторож не видел вовсе.
_tg_bad = []
_scanned = [f for suf in GUARDED_SUFFIXES for f in ROOT.rglob(f"*{suf}")]
for _py in _scanned:
    _rel = str(_py.relative_to(ROOT))
    if _rel in _TG_ALLOW or "/tests/" in _rel or _rel.startswith("tests/") or "__pycache__" in _rel:
        continue
    _src = _py.read_text(encoding="utf-8", errors="ignore")
    # утечка = ЧТЕНИЕ секрет-файла из хардкод-пути (.health_secrets) без env-aware.
    # (имена колонок схемы telegram_chat_id не ловим — нужен литерал .health_secrets)
    # env-aware доказывается либо литералом HEALTH_SECRETS_DIR, либо единым резолвером
    # secrets_dir() (secrets_paths.py). Литерал .health_secrets тут ещё бывает от
    # ГЛОБАЛЬНОГО anthropic_key — он общий на всех тенантов, не утечка.
    # ИМЕНА — ИЗ РЕЕСТРА, НЕ ИЗ ПРЕДИКАТА (2026-09-02): было три имени в `if`, и
    # календарь партнёра с location_ingest_token не охранялись ничем.
    _tenant_names = [n for n, sc in SECRET_SCOPE.items() if sc == "tenant"]
    if (".health_secrets" in _src and any(n in _src for n in _tenant_names)
            and "HEALTH_SECRETS_DIR" not in _src and "secrets_dir(" not in _src):
        _tg_bad.append(_rel)
check("per-tenant секреты по реестру SECRET_SCOPE (HEALTH_SECRETS_DIR)", not _tg_bad,
      f"хардкод без HEALTH_SECRETS_DIR (кросс-тенант утечка): {_tg_bad}" if _tg_bad else "")

# ── Секрет, которого никто не читает (2026-09-02) ────────────────────────────
# Находка: read_token лежал в каталоге с апреля, последнее ОБРАЩЕНИЕ к файлу
# 2026-04-09, читателя нет ни в репозитории, ни на машине. Мёртвый секрет — не
# мелочь: он выглядит как действующий доступ, его копируют при раскатке и держат
# в бэкапах, а отозвать забывают. WARN, не ошибка: сканер видит ЛИТЕРАЛЫ, и имя,
# собранное через переменную, дало бы ложную смерть — вердикт за человеком.
# Вердикт вынесен 21.09: read_token выведен из реестра (нить read-token-retire).
_declared = {n for n, sc in SECRET_SCOPE.items() if sc != "state"}
_secret_sources = [f.read_text(encoding="utf-8", errors="ignore")
                   for suf in GUARDED_SUFFIXES for f in ROOT.rglob(f"*{suf}")
                   if "__pycache__" not in str(f)]
# ДВЕ ФОРМЫ ЧТЕНИЯ, А НЕ ОДНА (16.09, BL-SECRET-READER-LITERAL-1). До этого WARN
# считал читателем только литерал, и пять имён почтового канала попадали в «без
# читателя» ЛОЖНО: notify._email_settings читает их через локальную переменную.
# Предупреждение, где пять строк из шести заведомо ложные, перестают читать.
# Форма с переменной засчитывается только в файле, работающем с каталогом секретов,
# и только для ОБЪЯВЛЕННОГО имени — иначе она спрятала бы мёртвый секрет (замер:
# без этого сужения форма ловит 77 пар вне реестра).
_read = (referenced_secret_names(_secret_sources)
         | referenced_via_variable(_secret_sources))
_unread = sorted(_declared - _read)
if _unread:
    warn("секреты без читателя",
         f"объявлены, но ни одна строка не читает: {_unread} — проверь и отзови. "
         "Обе формы чтения учтены (литерал и переменная в файле-с-секретами), "
         "поэтому ложным это уже не назвать")


# ── Итог ──────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
total = passed + len(errors)
print(f"Итог: {passed}/{total} проверок прошли")
if warnings:
    print(f"Предупреждений: {len(warnings)} (tech debt, не блокируют)")
if errors:
    print(f"\n{FAIL} ОШИБКИ ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"\n{PASS} Все контракты в порядке.")
    sys.exit(0)
