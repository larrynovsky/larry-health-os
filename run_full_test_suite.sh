#!/bin/bash
# run_full_test_suite.sh — ночной оркестратор тестов Health OS.
#
# Запускается через launchd `com.larry.health.test-suite` в 00:00 по местному времени.
# См. TEST_ARCHITECTURE.md §11.
#
# Запускает слои pyramid в порядке (от быстрых к медленным):
#   1. unit         — изолированные модули
#   2. integration  — связки через in-memory SQLite
#   3. consistency  — RYW / staleness / tentative / single-primary / causal / eventual
#   4. e2e_mock     — сквозные сценарии с моками внешних API
#   5. snapshot     — self-consistency для lab_extractor (UC-A-01) — 2 прогона
#   6. llm_judge    — D-уровень оракул через Haiku (платно по API)
#
# Каждый слой пишет свой junit.xml. Финальный summary.json для test_failure_handler.
#
# Exit codes:
#   0 = все слои зелёные
#   1 = какой-то слой имеет failures
#   2 = критическая ошибка инфры (pytest не запустился, БД недоступна, etc.)
#
# Логи: ~/health_test_suite.log (на Studio через launchd).
# Артефакты: tests/reports/{YYYY-MM-DD}/

set -uo pipefail

# Файловые дескрипторы: macOS soft limit по умолчанию 256.
# Integration слой после Consolidation (2026-05-10) имеет 74+ теста, каждый
# открывает SQLite + tmpdir → к концу очереди упор в лимит → false-negative
# "Too many open files". Поднимаем до 4096 на время прогона.
# Истинная утечка fds — отдельный backlog-item (BUG-FD-LEAK-INTEGRATION).
ulimit -n 4096

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Интерпретатор — тот же шов, что в run_checks.sh: в контейнере HEALTH_PY=python3, пути Homebrew
# там нет (до 03.10 эта задача в контейнере не запускалась с 30.09, нить host-container-split).
PY="${HEALTH_PY:-/opt/homebrew/bin/python3.11}"
TODAY="$(date +%Y-%m-%d)"
REPORTS_DIR="$SCRIPT_DIR/tests/reports/$TODAY"
LOG="$SCRIPT_DIR/logs/test_suite.log"

# ── Deprecation-ратчет (этап 4 плана agents_md_adoption, 2026-07-06) ─────────
# DeprecationWarning = ошибка, но ТОЛЬКО в ночном прогоне (здесь), НЕ в
# pytest.ini: глобальный конфиг зацепил бы pre-commit smoke на MacBook, где
# другая среда и чужие варнинги блокировали бы коммиты (feedback_runchecks).
# Baseline 2026-07-06: собственных deprecation'ов НОЛЬ (staging-прогон 2079 pass).
# Third-party ignores: каждый пункт с обоснованием; bump зависимости, принёсший
# новый warning, чинится пополнением списка, НЕ откатом ратчета
# (docs/how-to/dependency_updates.md).
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:-} -W error::DeprecationWarning -W 'ignore:builtin type swigvarlink:DeprecationWarning'"
# swigvarlink: C-расширение (SWIG) на shutdown интерпретатора, sys:1 — не наш код.

mkdir -p "$REPORTS_DIR" "$SCRIPT_DIR/logs"

# ── Утилиты ───────────────────────────────────────────────────────────────────

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

run_layer() {
    local layer="$1"           # unit | integration | consistency | e2e_mock | snapshot | llm_judge
    local extra_args="${2:-}"  # дополнительные pytest флаги
    local junit="$REPORTS_DIR/${layer}_junit.xml"

    # Маппинг layer → директория. Большинство совпадает (unit→tests/unit/), но:
    #   snapshot → tests/snapshots/  (исторически plural; см. UC-A-01 self-consistency)
    local dir
    case "$layer" in
        snapshot) dir="snapshots" ;;
        *)        dir="$layer"    ;;
    esac

    log "▶ layer=$layer (dir=tests/$dir/)"

    # Периметр слоя задаёт КАТАЛОГ, а не метка. До 15.09 здесь стоял отбор по имени
    # слоя (-m «$layer» — ёлочки, чтобы прозой не спорить со сторожем замысла, который
    # стережёт отсутствие той самой строки в коде), и файл без
    # `pytestmark = pytest.mark.<слой>` молча выпадал из ночного прогона.
    # Замер на Studio (14–15.09, --collect-only): unit брал 2363 теста из 4267,
    # integration 335 из 451, consistency 524 из 559. Отсеянные прогнали отдельно —
    # 1898 + 115 + 37 passed, НОЛЬ красных: фильтр выкидывал не больные тесты, а
    # невидимые. Расхождение двух домов ответа на вопрос «какого слоя этот тест»
    # (каталог и метка) выглядело снаружи как зелёный прогон — поэтому и жило.
    # Метки слоёв остаются живыми для ручных прогонов (`pytest -m consistency`);
    # ночной отбор на них больше не опирается.
    #
    # Исключение одно и названо: платный слой включает свои тесты сам — его вызов
    # ниже идёт с `--override-ini=addopts=`, и подставить туда «not
    # requires_anthropic_key» значило бы выключить ровно то, ради чего слой есть.
    local mfilter
    if [ "$layer" = "llm_judge" ]; then
        mfilter="llm_judge"
    else
        mfilter="not requires_anthropic_key"
    fi

    if "$PY" -m pytest "tests/$dir/" \
        -m "$mfilter" \
        --junitxml="$junit" \
        $extra_args \
        >> "$LOG" 2>&1; then
        log "✅ $layer passed"
        return 0
    else
        local code=$?
        log "❌ $layer failed (exit $code)"
        return "$code"
    fi
}

# ── Префлайт ─────────────────────────────────────────────────────────────────

log ""
log "═══ Test suite started ═══"

if ! "$PY" -c "import pytest" 2>/dev/null; then
    log "CRITICAL: pytest not installed"
    exit 2
fi

if [ ! -f "$SCRIPT_DIR/pytest.ini" ]; then
    log "CRITICAL: pytest.ini not found"
    exit 2
fi

# ── Запуск слоёв ─────────────────────────────────────────────────────────────

OVERALL_EXIT=0

# 1. Unit — самые быстрые, должны быть зелёными всегда
if ! run_layer unit; then
    OVERALL_EXIT=1
fi

# 2. Integration
if [ -d "$SCRIPT_DIR/tests/integration" ] && ls "$SCRIPT_DIR/tests/integration"/test_*.py >/dev/null 2>&1; then
    if ! run_layer integration; then
        OVERALL_EXIT=1
    fi
fi

# 3. Consistency
if [ -d "$SCRIPT_DIR/tests/consistency" ] && ls "$SCRIPT_DIR/tests/consistency"/test_*.py >/dev/null 2>&1; then
    if ! run_layer consistency; then
        OVERALL_EXIT=1
    fi
fi

# 4. E2E mock
if [ -d "$SCRIPT_DIR/tests/e2e_mock" ] && ls "$SCRIPT_DIR/tests/e2e_mock"/test_*.py >/dev/null 2>&1; then
    if ! run_layer e2e_mock; then
        OVERALL_EXIT=1
    fi
fi

# 5. Snapshot (self-consistency, не классические snapshot — UC-A-01 двойной прогон)
if [ -d "$SCRIPT_DIR/tests/snapshots" ] && ls "$SCRIPT_DIR/tests/snapshots"/test_*.py >/dev/null 2>&1; then
    if ! run_layer snapshot; then
        OVERALL_EXIT=1
    fi
fi

# 6. LLM-judge — платно по Anthropic API. Запускаем только если есть ключ.
if [ -f "$HOME/.health_secrets/anthropic_key" ] && \
   [ -d "$SCRIPT_DIR/tests/llm_judge" ] && \
   ls "$SCRIPT_DIR/tests/llm_judge"/test_*.py >/dev/null 2>&1; then
    if ! run_layer llm_judge "--override-ini=addopts="; then
        OVERALL_EXIT=1
    fi
fi

# ── Итоговый summary.json ────────────────────────────────────────────────────

"$PY" - <<PYEOF
import json
import xml.etree.ElementTree as ET
from pathlib import Path

reports_dir = Path("$REPORTS_DIR")
total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
per_layer = {}

for junit_path in sorted(reports_dir.glob("*_junit.xml")):
    layer = junit_path.stem.replace("_junit", "")
    try:
        root = ET.parse(junit_path).getroot()
        # JUnit может иметь testsuites или testsuite напрямую
        suites = root.findall(".//testsuite") or [root]
        ts = sum(int(s.get("tests", 0)) for s in suites)
        fl = sum(int(s.get("failures", 0)) for s in suites)
        er = sum(int(s.get("errors", 0)) for s in suites)
        sk = sum(int(s.get("skipped", 0)) for s in suites)
        per_layer[layer] = {"tests": ts, "failures": fl, "errors": er, "skipped": sk}
        total["tests"] += ts
        total["failures"] += fl
        total["errors"] += er
        total["skipped"] += sk
    except Exception as e:
        per_layer[layer] = {"error": str(e)}

summary = {
    "date": "$TODAY",
    "overall_exit": $OVERALL_EXIT,
    "total": total,
    "per_layer": per_layer,
}
(reports_dir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
PYEOF

log "═══ Test suite finished (exit=$OVERALL_EXIT) ═══"

# ── Post-suite handlers ─────────────────────────────────────────────────────
# T-0.11/12/13: triage failures (retry, diagnosis, TG-алерт, Reminder)
log "▶ test_failure_handler"
"$PY" "$SCRIPT_DIR/test_failure_handler.py" >> "$LOG" 2>&1 || \
    log "WARN: test_failure_handler exited non-zero"

# T-0.14: morning_test_summary → agent_reports type='test_summary'
log "▶ morning_test_summary"
"$PY" "$SCRIPT_DIR/morning_test_summary.py" >> "$LOG" 2>&1 || \
    log "WARN: morning_test_summary exited non-zero"

log "═══ Post-suite handlers done ═══"

exit "$OVERALL_EXIT"
