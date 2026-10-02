#!/bin/bash
# run_checks.sh — пайплайн проверки и документирования Health OS.
#
# Режимы:
#   bash run_checks.sh              # полный пайплайн (post-commit)
#   bash run_checks.sh --skip-doc   # только тесты, без doc_agent
#   bash run_checks.sh --doc-only   # только doc_agent
#   bash run_checks.sh --scheduled  # только integrity_tests (launchd 07:50)
#                                   # exit 0=OK, 1=WARN, 2=CRITICAL
#                                   # (запись в /tmp снята 2026-08-11: не читалась)
#
# Вызывается из:
#   .git/hooks/post-commit                        — полный режим
#   launchd com.larry.health.integrity-check      — --scheduled (07:50)
# Логи: health_scripts/logs/run_checks.log

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/logs/run_checks.log"
# Q-4 fix (2026-05-22, roadmap): полный путь — post-commit hook и ssh
# non-interactive shell не имеют /opt/homebrew/bin в PATH → exit 127.
# launchd plist выставляет PATH явно, поэтому ранее работало только через scheduled.
# HEALTH_PY — единственный шов, через который этот скрипт вообще проверяем:
# без него оракул артефакта пришлось бы писать на КОПИИ логики, то есть стеречь
# не тот код, что исполняется ночью (2026-08-10).
PY="${HEALTH_PY:-/opt/homebrew/bin/python3.11}"
SKIP_DOC=false
DOC_ONLY=false
SCHEDULED=false

# smoke_tests делает write'ы в health.db. БД — single-source на Studio (R1/R2,
# split-brain fix 2026-06-18); на не-Studio её НЕТ, и создавать в iCloud нельзя
# (иначе воскрешаем удалённый форк). Поэтому ALLOW_WRITE и smoke — только на Studio.
if "$PY" "$SCRIPT_DIR/infra_config.py" is-primary; then  # основная машина — private/infra.yaml
    export ALLOW_WRITE_NONPRIMARY=1
    # R1 activation (multitenancy Phase 0): делаем тенанта явным, чтобы пайплайн
    # не висел на тихом hostname-дефолте (равен этому же пути). Респектит внешний
    # HEALTH_DATA_DIR (per-tenant прогон), иначе — канон владельца.
    export HEALTH_DATA_DIR="${HEALTH_DATA_DIR:-$HOME/health}"
fi

for arg in "$@"; do
    [[ "$arg" == "--skip-doc"  ]] && SKIP_DOC=true
    [[ "$arg" == "--doc-only"  ]] && DOC_ONLY=true
    [[ "$arg" == "--scheduled" ]] && SCHEDULED=true
done

mkdir -p "$SCRIPT_DIR/logs"
echo "" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') === run_checks запущен (mode: $*) ===" >> "$LOG"

cd "$SCRIPT_DIR"

# ── Утилиты ───────────────────────────────────────────────────────────────────

record_fault() {
    "$PY" -c 'import notify, sys; notify.fault("run_checks: " + sys.argv[1], person_key=None)' "$1"
}

# --- pytest_diff begin ---
# Разница «новое / хроническое» между сегодняшним и прошлым красным (2026-08-30).
# Замер: test_clone_is_skipped_and_says_so был красным КАЖДЫЙ ДЕНЬ с 2026-07-30 (31 алерт),
# и алерт «pytest упал» стал фоном; новый красный (ратчет 30.08) в нём не отличался от
# старого. Алерт обязан называть, что упало ВПЕРВЫЕ, и сколько дней держится остальное.
# Состояние: $1 — файл «node-id<TAB>дата-первого-провала», $2 — файл с сегодняшними id
# (по строке). Печатает одну строку для алерта, переписывает состояние.
pytest_failed_diff() {
    # ПУСТАЯ СТРОКА = МОЛЧИМ (2026-09-02, решение владельца). Прежняя версия печатала
    # «хроническое» каждый день, и алерт приходил на 42 прогонах из 70 — канал, который
    # срабатывает чаще, чем не срабатывает, перестаёт быть сигналом. Теперь говорим
    # только об ИЗМЕНЕНИИ состояния: новый id; возраст ≥3 дней (первое напоминание);
    # возраст ≥7 дней (второе, последнее). Пороги — решение владельца 02.09.
    # Состояние: id<TAB>дата-первого-провала<TAB>сколько напоминаний уже отправлено.
    local state="$1" today_ids="$2" today epoch
    # Обе точки — ПОЛДЕНЬ своей даты (23.09): BSD `date -j -f '%Y-%m-%d'` подставляет в
    # разобранную дату ТЕКУЩЕЕ время суток, и секунда, щёлкнувшая между двумя вызовами date,
    # делала из трёх суток 259199 с = «2 дня» — напоминание не уходило (тест
    # test_third_day_escalates_once краснел через раз). Полдень + округление держат и переход
    # на зимнее время (сутки в 23/25 ч).
    # BSD (-j -f, macOS) или GNU (-d, образ контейнера — замер 30.09: там -j нет).
    _noon() { date -j -f '%Y-%m-%d %H:%M:%S' "$1 12:00:00" '+%s' 2>/dev/null || date -d "$1 12:00:00" '+%s' 2>/dev/null; }
    today="$(date '+%Y-%m-%d')"; epoch="$(_noon "$today")"
    local new="" esc="" id first n days fs
    [ -f "$state" ] || : > "$state"
    while read -r id; do
        [ -n "$id" ] || continue
        first="$(awk -F'\t' -v i="$id" '$1==i{print $2}' "$state")"
        n="$(awk -F'\t' -v i="$id" '$1==i{print $3}' "$state")"
        case "$n" in ''|*[!0-9]*) n=0 ;; esac
        if [ -z "$first" ]; then
            new="${new}${id} "; first="$today"; n=0
        else
            fs="$(_noon "$first" || echo "$epoch")"
            days=$(( (epoch - fs + 43200) / 86400 ))
            # ВОЗРАСТ, А НЕ «РОВНО N-Й ДЕНЬ»: пропущенный прогон не должен съесть
            # напоминание навсегда. Флаг n делает напоминание одноразовым.
            if   [ "$n" -lt 1 ] && [ "$days" -ge 3 ]; then esc="${esc}${id} (${days} дн) "; n=1
            elif [ "$n" -lt 2 ] && [ "$days" -ge 7 ]; then esc="${esc}${id} (${days} дн) "; n=2
            fi
        fi
        printf '%s\t%s\t%s\n' "$id" "$first" "$n"
    done < "$today_ids" > "${state}.next"
    mv "${state}.next" "$state"
    local out=""
    [ -n "$new" ] && out="🆕 НОВОЕ: ${new}"
    [ -n "$esc" ] && out="${out}⏳ ВИСИТ: ${esc}"
    printf '%s' "$out"
}
pytest_failed_ids() {
    # Имена упавших из вывода КРАСНОГО прогона. КРАСНЫЙ БЕЗ ИМЁН = ПРОГОН НЕ СОСТОЯЛСЯ (30.09):
    # INTERNALERROR или падение сбора не дают ни одной строки FAILED, пустой набор «не изменился»
    # против пустого — и мёртвый набор тестов молчал (первое утро в контейнере: 0 тестов прогнано).
    # Такой исход получает своё имя и проходит через ту же хронику, что обычные падения.
    local ids
    ids="$(grep '^FAILED ' "$1" | sed 's/^FAILED //; s/ .*//' | sort -u)"
    if [ -n "$ids" ]; then printf '%s\n' "$ids"; else echo "pytest-не-запустился"; fi
}
# --- pytest_diff end ---

run_step() {
    local name="$1"
    local cmd="$2"
    echo "$(date '+%H:%M:%S') ▶ $name" >> "$LOG"
    if eval "$cmd" >> "$LOG" 2>&1; then
        echo "$(date '+%H:%M:%S') ✅ $name" >> "$LOG"
        return 0
    else
        local exit_code=$?
        echo "$(date '+%H:%M:%S') ❌ $name (exit $exit_code)" >> "$LOG"
        return $exit_code
    fi
}

parse_int() {
    echo "$1" | $PY -c "import sys,json; print(json.load(sys.stdin).get('$2',0))" 2>/dev/null || echo "0"
}

# ── Scheduled mode (launchd 07:50) ───────────────────────────────────────────
if [[ "$SCHEDULED" == "true" ]]; then
    # ЧЕЛОВЕК, НЕ ВЛАДЕЛЕЦ (28.09, нить tb-checks). Плист проверки теперь копируется каждому
    # человеку (install.py --tenant). Общие артефакты владельца (logs/integrity_latest.json,
    # квитанции триажа) и прогон кода (он один на машину) тенант не трогает: его вердикт
    # лежит в <DATA>/logs, а находки уходят ОПЕРАТОРУ строкой имён проверок — решение
    # владельца 28.09 («находки по данным партнёра — мне»), без значений из данных.
    # Ветку тенанта открывает только УТВЕРДИТЕЛЬНЫЙ ответ «данные не владельца»: сбой самой
    # проверки (нет модуля, упал импорт) оставляет прежний путь владельца, а не уводит
    # его вердикт в чужой каталог.
    _WHOSE="$("$PY" -c "import secrets_paths as s; print('owner' if s.is_owner_data() else 'tenant')" 2>/dev/null)"
    if [[ "$_WHOSE" == "tenant" ]]; then
        TLOGS="$HEALTH_DATA_DIR/logs"; mkdir -p "$TLOGS"
        TAG="$(basename "$HEALTH_DATA_DIR")"
        LOG="$TLOGS/run_checks.log"
        TJSON=$($PY integrity_tests.py --json 2>/dev/null)
        if echo "$TJSON" | $PY -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
            echo "$TJSON" > "$TLOGS/integrity_latest.json"
            # СВОЙ УТРЕННИЙ РАЗБОР (28.09, решение владельца «вариант А»): вопросы, на которые
            # отвечает только сам человек (анализы давно не сдавались, истёк период), — в ЕГО дом
            # вопросов; технические находки остаются оператору строкой имён (ниже). Артефакты
            # триажа — в данных тенанта, не рядом с владельческими (HEALTH_TRIAGE_LOGS).
            if HEALTH_TRIAGE_LOGS="$TLOGS" "$PY" "$SCRIPT_DIR/triage_agent.py" --send >> "$LOG" 2>&1; then
                echo "$(date '+%H:%M:%S') ✅ triage [$TAG] отработал" >> "$LOG"
            else
                echo "$(date '+%H:%M:%S') ❌ triage [$TAG] упал (exit $?)" >> "$LOG"
            fi
            SUMMARY=$(echo "$TJSON" | $PY -c "import sys,json; n=[f[0] for f in json.load(sys.stdin).get('failures') or []]; print('; '.join(n)[:900])")
        else
            SUMMARY="вывод монитора не разбирается — вердикт не обновлён"
        fi
        echo "$(date '+%Y-%m-%d %H:%M:%S') [scheduled $TAG] ${SUMMARY:-0 FAIL}" >> "$LOG"
        if [[ -n "$SUMMARY" ]]; then
            "$PY" -c 'import notify; notify.fault("run_checks: tenant integrity failed; see tenant artifact", person_key=None)' || true
        fi
        $PY oura_freshness_check.py --notify >> "$LOG" 2>&1 || true
        $PY model_health_check.py --daily --notify >> "$LOG" 2>&1 || true
        $PY -c "import memory_truthcheck as m; print('A1', m.apply_confirmations()['bumped'])" >> "$LOG" 2>&1 || true
        exit 0
    fi
    # ЗАХВАТ И ФОЛБЭК — РАЗНЫЕ ШАГИ (2026-08-10). Было одной строкой:
    #     INTEGRITY_JSON=$($PY integrity_tests.py --json || echo '{"fail":1,...}')
    # `$(A || B)` не выбирает между A и B: оно исполняет A, забирает её stdout и
    # при ненулевом коде ДОПИСЫВАЕТ к нему stdout B. Монитор возвращает ненулевой
    # код именно когда есть падения — значит артефакт становился нечитаемым
    # (два JSON-документа подряд, `Extra data: line 73`) РОВНО в тот прогон,
    # ради которого триаж и существует. Замер 2026-08-10: так и лежало.
    INTEGRITY_JSON=$($PY integrity_tests.py --json 2>/dev/null)
    [[ -n "$INTEGRITY_JSON" ]] || INTEGRITY_JSON='{"fail":1,"pass":0,"warn":0}'
    FAIL_COUNT=$(parse_int "$INTEGRITY_JSON" "fail")
    WARN_COUNT=$(parse_int "$INTEGRITY_JSON" "warn")
    PASS_COUNT=$(parse_int "$INTEGRITY_JSON" "pass")

    echo "$(date '+%Y-%m-%d %H:%M:%S') [scheduled] ${PASS_COUNT}P ${FAIL_COUNT}F ${WARN_COUNT}W" >> "$LOG"

    # НЕЧИТАЕМЫЙ СВЕЖИЙ ХУЖЕ ЧИТАЕМОГО ВЧЕРАШНЕГО: артефакт перезаписывается,
    # только если он разбирается. Иначе прежний остаётся на месте, а в лог идёт
    # крик — иначе триаж молча получил бы мусор и «нашёл 0 проблем».
    if echo "$INTEGRITY_JSON" | $PY -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "$INTEGRITY_JSON" > "$SCRIPT_DIR/logs/integrity_latest.json"
        # ТРИАЖ — ЦЕПОЧКОЙ, СРАЗУ ЗА АРТЕФАКТОМ (2026-09-01). Плист триажа стоял на
        # 08:00, а артефакт здесь появляется в 08:25–08:27 — и триаж каждый день
        # доставлял владельцу вчерашние находки под сегодняшней датой. Здесь
        # порядок гарантирован процессом, а не часами; плист остаётся запасным
        # входом, он не ставит маркер на вчерашнем артефакте (require_today).
        if "$PY" "$SCRIPT_DIR/triage_agent.py" --send >> "$LOG" 2>&1; then
            echo "$(date '+%H:%M:%S') ✅ triage доставлен цепочкой" >> "$LOG"
        else
            echo "$(date '+%H:%M:%S') ❌ triage цепочкой упал (exit $?) — смотри logs/triage.log" >> "$LOG"
        fi
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: вывод integrity не разбирается — "\
"артефакт НЕ перезаписан, триаж возьмёт прежний" >> "$LOG"
    fi

    # Датчик свежести биометрии (Oura/Apple): ИМЕНОВАННЫЙ алерт при реальном
    # обрыве (age > conit-лимит×2). Закрывает дыру: integrity кладёт стейл в
    # 'failures', а triage форвардит только 'warnings'. См. oura_freshness_check.py.
    $PY oura_freshness_check.py --notify >> "$LOG" 2>&1 || true

    # Жизненный цикл LLM-моделей (2026-10-01, нить llm-provider): ЕЖЕДНЕВНО, не раз в месяц.
    # Снимок доступности читает hai_core.get_model; отозвана активная модель — переход на
    # следующую допущенную из цепочки model.<role> и уведомление владельцу (его решение 01.10).
    $PY model_health_check.py --daily --notify >> "$LOG" 2>&1 || true
    # Допуск преемников к цепочкам (2026-10-02): раз в 7 дней по метке, в пределах бюджета
    # владельца llm.admission.budget (нет бюджета — не тратит). Только у владельца данных:
    # у тенанта цепочки пока не наследуются (долг BL-LLM-CHAIN-PARITY-1).
    $PY llm_admission.py --weekly --notify >> "$LOG" 2>&1 || true

    # A1 (2026-07-07): бамп confirmations на подтверждённых каноном state (идемпотентно).
    # Заполняет пустую сетку confirmations (аудит 0/501 — «производитель сетки»); авто-
    # безопасно (счётчик, value/класс не трогает). Промоут/супёрсид — человек-гейт, отдельно.
    $PY -c "import memory_truthcheck as m; r=m.apply_confirmations(); print('A1 confirmations bumped:', r['bumped'], 'из', r['confirmed_total'], 'подтверждённых; disagree:', r['disagree_ids'])" >> "$LOG" 2>&1 || true

    # E1 (2026-06-23): полный регрессионный pytest — ТОЛЬКО в scheduled (07:50),
    # не в post-commit (+84с к каждому коммиту). Закрывает дыру: раньше полный
    # suite не гонялся пайплайном вообще (см. docs/explanation/test_architecture.md).
    PYTEST_OK=true
    # Без git (образ контейнера) sha берётся из манифеста образа — иначе алерт «HEAD ?» (30.09).
    _PYTEST_HEAD="$(git rev-parse --short HEAD 2>/dev/null || $PY -c 'import git_facts; print(git_facts.head_sha())' 2>/dev/null || echo '?')"
    # СОСТОЯНИЕ В СВОЁМ ДЕРЕВЕ, НЕ В ОБЩЕМ /tmp (2026-09-02). Прежний абсолютный путь
    # делили ДВА чекаута: красный прогон в ~/health_staging (там нет .git → HEAD «?»)
    # писал состояние, боевой зелёный читал его и слал владельцу «снова зелёный (был
    # красным на ?)» — о поломке, которой в бою не было. Реализовалось 02.09.
    _PYTEST_FAIL_STATE="$SCRIPT_DIR/logs/pytest_recovery_state"
    _PYTEST_OUT="$(mktemp -t health_pytest.XXXXXX)"
    # -rf: короткая сводка со списком FAILED node-id → алерт называет, ЧТО именно сгнило
    # (какой датчик), а не «что-то в pytest упало». Гранулярность для ВСЕХ consistency-датчиков.
    if $PY -m pytest tests/ -q --tb=no -rf > "$_PYTEST_OUT" 2>&1; then
        cat "$_PYTEST_OUT" >> "$LOG"
        echo "$(date '+%H:%M:%S') ✅ pytest tests/ OK (HEAD ${_PYTEST_HEAD})" >> "$LOG"
        # E1r (2026-07-23): recovery-пинг. Если прошлый прогон был красным — закрываем петлю.
        if [ -f "$_PYTEST_FAIL_STATE" ]; then
            _PREV_FAIL="$(cat "$_PYTEST_FAIL_STATE" 2>/dev/null)"
            "$PY" -c 'import notify, i18n; notify.weekly(i18n.t("owner.weekly.green"))' 
            rm -f "$_PYTEST_FAIL_STATE"
        fi
        rm -f "$SCRIPT_DIR/logs/pytest_failed_state.tsv"   # зелёный обнуляет хронику
    else
        cat "$_PYTEST_OUT" >> "$LOG"
        echo "$(date '+%H:%M:%S') ❌ pytest tests/ FAILED (HEAD ${_PYTEST_HEAD})" >> "$LOG"
        PYTEST_OK=false
        WARN_COUNT=$((WARN_COUNT + 1))
        # E1s (2026-07-23): самодатируемый алерт (sha+время) + именование упавших датчиков.
        _FAILED_LIST="$(mktemp -t health_pytest_ids.XXXXXX)"
        pytest_failed_ids "$_PYTEST_OUT" > "$_FAILED_LIST"
        # Новое против хронического: состояние в logs/ (не /tmp — macOS чистит его, и
        # «хроническое» каждый раз рождалось бы заново как «новое»).
        _FAILED_DIFF="$(pytest_failed_diff "$SCRIPT_DIR/logs/pytest_failed_state.tsv" "$_FAILED_LIST" | cut -c1-900)"
        rm -f "$_FAILED_LIST"
        # МОЛЧАНИЕ — ЛЕГАЛЬНЫЙ ИСХОД: набор упавших не изменился, нового факта нет.
        # Состояние recovery пишется ТОЛЬКО здесь, поэтому «снова зелёный» закрывает
        # ровно ту петлю, которую владельцу открыли, и не выдумывает своих.
        if [ -n "$_FAILED_DIFF" ]; then
            record_fault "pytest regression; see run_checks.log"
            echo "${_PYTEST_HEAD} @ $(date '+%Y-%m-%d %H:%M')" > "$_PYTEST_FAIL_STATE"
        else
            echo "$(date '+%H:%M:%S') ⏭ pytest красный, но набор упавших не изменился — молчим" >> "$LOG"
        fi
    fi
    rm -f "$_PYTEST_OUT"

    if [[ "$FAIL_COUNT" -gt 0 ]]; then
        # СООБЩЕНИЕ ГОВОРИТ ТО, ЧТО ПРОИСХОДИТ (2026-08-11). Прежний текст —
        # «утренний отчёт заблокирован» — был неправдой, и приходил каждое утро,
        # когда есть хоть один FAIL. Замер: бриф делает бот
        # (`jobs/scheduled.py::send_morning_report`, очередь задач, 08:30), слова
        # «integrity» в том файле нет вовсе, и 11.08 бриф был построен и доставлен
        # (BRIEF_GATE diag 08:30:22 и 08:30:26, context_cards 18 карточек).
        # `exit 2` ниже заканчивает ЭТОТ скрипт и больше ничего.
        #
        # Цена прежнего текста выше, чем кажется: неверная тяжесть учит игнорировать
        # сигнал, а ложное описание ПОСЛЕДСТВИЯ учит не верить тому, чем система
        # объясняет себя человеку. Второе не восстанавливается фактами.
        record_fault "integrity findings; see integrity_latest.json"
        echo "$(date '+%H:%M:%S') FAIL:${FAIL_COUNT} — сбой записан в журнал" >> "$LOG"
        exit 2
    fi

    # Dead-man's switch (2026-06-28, калибровка 2026-07-02): пинг внешнего
    # healthchecks.io. Задача сторожа — ловить СМЕРТЬ системы (не запустилась,
    # Studio/сеть легли), а НЕ наличие предупреждений. Поэтому пингуем и на
    # benign-WARN (tech-debt: свежесть, backlog, lifecycle-убыль) — иначе хронический
    # WARN:5 навсегда глушил бы пинг и dead-man слал бы ложный email ежедневно.
    # НЕ пингуем при CRITICAL (exit 2 выше — вышли) и при упавшем pytest (логическая
    # поломка). Пинг = «система жива и не в критике»; отсутствие пинга сутки+grace →
    # EMAIL (канал, независимый от Telegram). URL — в секрете (не в репо).
    if [[ "$PYTEST_OK" == "true" ]]; then
        # ЧЕРЕЗ ДОМ СЕКРЕТОВ (2026-09-02): хардкод $HOME/.health_secrets не изолируется
        # переменной HEALTH_SECRETS_DIR, и прогон в песочнице пинговал БОЕВОЙ dead-man —
        # «монитор жив», когда боевой мёртв. Ложная живость, §14.
        _HC_URL="$("$PY" -c 'from secrets_paths import secrets_dir; print((secrets_dir()/"healthcheck_url").read_text().strip())' 2>/dev/null)"
        [ -n "$_HC_URL" ] && curl -fsS -m 10 --retry 3 "$_HC_URL" >/dev/null 2>&1 || true
    fi

    # СОСТОЯНИЕ В /tmp БОЛЬШЕ НЕ ПИШЕТСЯ (2026-08-11). Файл
    # `/tmp/health_integrity_status` писался в трёх местах этого скрипта и НЕ
    # ЧИТАЛСЯ НИГДЕ — grep по *.py, *.sh, *.plist давал только записи. Осиротевшее
    # состояние удалено, а не наделено читателем, и это решение, а не уборка:
    #
    #  · права. `/tmp` — общий каталог; файл создавался с umask по умолчанию, и
    #    читатель прав не проверял бы. Подделанный «OK» спрятал бы настоящую
    #    критику (WSTG-CONF-09, шаблон «state written to /tmp without permission
    #    checks»);
    #  · свежесть. У записи нет ни времени, ни порога устаревания. Не отработал
    #    монитор в 07:50 — в 08:30 читатель взял бы вчерашний «OK» и принял отказ
    #    за норму. Отклонение по устареванию не ограничено ничем.
    #
    # Если блокировка когда-нибудь понадобится по-настоящему — её место в том же
    # процессе, где строится бриф, с явной границей свежести, а не в файле.
    if [[ "$WARN_COUNT" -gt 0 ]]; then
        echo "$(date '+%H:%M:%S') WARN:${WARN_COUNT} — в integrity_latest.json, triage запущен цепочкой выше" >> "$LOG"
        exit 1
    fi

    echo "$(date '+%H:%M:%S') ✅ integrity OK" >> "$LOG"
    exit 0
fi

# ── Полный пайплайн (post-commit / manual) ────────────────────────────────────
TESTS_PASSED=true

# ВЕСЬ пайплайн ниже (check_contracts, smoke, integrity, arch_guard, doc_agent)
# импортирует health_db и требует среды Studio: локальная health.db + numpy/markdown.
# На MacBook health_db падает по guard'у R1/R2 при импорте → каждый шаг крашится и слал
# ЛОЖНЫЕ алерты на каждом post-commit (раньше умирал на check_contracts первым).
# Пайплайн — ТОЛЬКО на Studio (scheduled integrity-check.plist + manual на Studio).
# MacBook post-commit лишь пушит на Studio; Studio проверяет запушенный код.
if ! "$PY" "$SCRIPT_DIR/infra_config.py" is-primary; then
    # MacBook = writer-узел: тесты здесь не могут (R1/R2, среда), но ДОКИ обязаны
    # бежать именно здесь — doc_agent пишет+stage'ит, ночной backup.sh коммитит.
    # До 2026-07-06 здесь был глухой exit 0 → doc_agent/arch_guard не бежали НИГДЕ
    # с 2026-06-18 (BL-DOCAGENT-DEAD-1): Studio-scheduled — integrity-ветка, а
    # полный пайплайн Studio-only. Ранний выход освободил собственный груз
    # (feedback_rule_self_application). Сторож: check_changelog_freshness (integrity).
    echo "$(date '+%H:%M:%S') ⏭ тесты пропущены — не-Studio хост; доки бегут здесь (writer-узел)" >> "$LOG"
    if [[ "$SKIP_DOC" == "false" ]]; then
        if run_step "arch_guard" "$PY arch_guard.py"; then
            echo "$(date '+%H:%M:%S') ✅ граф зависимостей проверен (MacBook)" >> "$LOG"
        else
            record_fault "arch_guard failed"
        fi
        # Английская карта (arch-en-gen, 29.09): тот же граф, английский блок и свой кэш событий.
        if run_step "arch_guard_en" "$PY arch_guard.py --lang en"; then
            echo "$(date '+%H:%M:%S') ✅ английский граф обновлён (MacBook)" >> "$LOG"
        else
            record_fault "arch_guard --lang en failed"
        fi
        if run_step "doc_agent" "$PY doc_agent.py"; then
            echo "$(date '+%H:%M:%S') ✅ документация обновлена (MacBook)" >> "$LOG"
        else
            record_fault "doc_agent failed"
        fi
    fi
    exit 0
fi

if [[ "$DOC_ONLY" == "false" ]]; then

    if ! run_step "check_contracts" "$PY check_contracts.py"; then
        TESTS_PASSED=false
        record_fault "check_contracts failed"
        exit 1
    fi

    if "$PY" "$SCRIPT_DIR/infra_config.py" is-primary; then
        if ! run_step "smoke_tests" "$PY smoke_tests.py"; then
            TESTS_PASSED=false
            record_fault "smoke_tests failed"
            exit 1
        fi
    else
        echo "  ⏭  smoke_tests — не-Studio хост (БД-write-проверки только на Studio, R1/R2)"
    fi

    # Тот же дефект, что в scheduled-ветке (2026-08-10): фолбэк ВНУТРИ подстановки
    # дописывался к настоящему выводу, и на прогоне с падениями `parse_int` читал
    # мусор → FAIL_COUNT=0 → пост-коммитное уведомление о падениях НЕ уходило.
    # Форма взята у `watch_and_test.sh`, где она с самого начала верная.
    INTEGRITY_JSON=$($PY integrity_tests.py --json 2>/dev/null)
    [[ -n "$INTEGRITY_JSON" ]] || INTEGRITY_JSON='{"fail":1,"pass":0,"warn":0}'
    FAIL_COUNT=$(parse_int "$INTEGRITY_JSON" "fail")
    WARN_COUNT=$(parse_int "$INTEGRITY_JSON" "warn")
    PASS_COUNT=$(parse_int "$INTEGRITY_JSON" "pass")

    echo "$(date '+%H:%M:%S') integrity: ${PASS_COUNT}P ${FAIL_COUNT}F ${WARN_COUNT}W" >> "$LOG"

    if [[ "$FAIL_COUNT" -gt 0 ]]; then
        FAILURES=$(echo "$INTEGRITY_JSON" | $PY -c "
import sys,json
d=json.load(sys.stdin)
lines=[str(f) for f in d.get('failures',[])[:3]]
print('\n'.join(lines))
" 2>/dev/null || echo "см. лог")
        record_fault "integrity findings; see integrity_latest.json"
        exit 1
    fi

    if [[ "$WARN_COUNT" -gt 0 ]]; then
        record_fault "integrity findings; see integrity_latest.json"
    fi

fi

if [[ "$SKIP_DOC" == "false" ]]; then
    if run_step "arch_guard" "$PY arch_guard.py"; then
        echo "$(date '+%H:%M:%S') ✅ граф зависимостей проверен" >> "$LOG"
    else
        # До 2026-06-29 здесь не было else: падение arch_guard уходило ТОЛЬКО в
        # run_checks.log (никто не читает). Теперь — громкий алерт, как у соседей.
        record_fault "arch_guard failed"
    fi
fi

# doc_agent на Studio НЕ бежит (2026-07-06): он stage'ит CHANGELOG/SECURITY, а
# staged-правки на Studio = dirty tree = сломанный updateInstead-push. Писатель
# доков — writer-узел (MacBook, ветка выше). Один узел = нет расхождения LLM-доков.
echo "$(date '+%H:%M:%S') ⏭ doc_agent — Studio deploy-only, доки пишет MacBook" >> "$LOG"

echo "$(date '+%Y-%m-%d %H:%M:%S') === run_checks завершён ===" >> "$LOG"
