#!/bin/bash
# test_on_studio.sh — прогнать регрессию КОДА (pytest) против СНАПШОТА канона на
# Studio, БЕЗ коммита и БЕЗ касания деплой-target (~/health_scripts на Studio).
#
# Закрывает корень BL-MULTISESSION-1 (CLAUDE.md §12, инцидент 2026-07-05):
# раньше проверить незакоммиченный код на реальных данных было негде, кроме правки
# Studio напрямую → грязный деплой-target → блок деплоя для всех сессий. Теперь
# WIP-дерево MacBook едет в ИЗОЛИРОВАННЫЙ ~/health_staging, там pytest гоняется
# против read-only копии health.db. Данные не покидают Studio (снапшот на том же
# диске, что канон) — приватность не страдает.
#
# Запуск:  ./scripts/test_on_studio.sh                              (полный прогон)
#          ./scripts/test_on_studio.sh tests/unit/test_x.py        (ЧАСТИЧНЫЙ)
#          ./scripts/test_on_studio.sh 'tests/unit -k rejected'    (ЧАСТИЧНЫЙ)
#
# ЗАЧЕМ СЕЛЕКТОР (2026-07-30, нить validation-gate-repair): доказательство, что оракул
# КРАСНЕЕТ, требует мутации, а мутации гоняются по одной — иначе покраснение не
# атрибутируется. Шесть мутаций × полный набор — неподъёмно; targeted-прогон в песочнице
# делает это секундами. Мутировать деплой-target на Studio НЕЛЬЗЯ (§12: грязное дерево →
# молчаливый отказ push → все сессии гоняют недеплоенный код), поэтому селектор гоняет
# ровно там же, где полный прогон — в изолированном staging.
#
# ЧАСТИЧНЫЙ ПРОГОН НЕ ЗАМЕНЯЕТ ПОЛНЫЙ и это не косметика формулировки: он не гоняет
# integrity code-guard и говорит о подмножестве. Отсюда — другой текст вердикта и
# запрет ставить им отметку о прогоне (датчик свежести считает только полные).
#
# ГРАНИЦЫ (осознанно):
#  - Гоняет `pytest tests/` — сеть регрессий КОДА (fixture-БД + чтение снапшота).
#  - НЕ гоняет integrity_tests.py — это монитор ЖИВЫХ данных (свежесть, cross-tenant,
#    split-brain). На суточном снапшоте он даёт ложь (устаревшая свежесть, «дубль
#    канона» = ложный cross-tenant). Живой монитор остаётся ночным на каноне
#    (run_checks.sh --scheduled, 07:50). Здесь мы ловим «плохой код», не «плохие данные».
#  - Staging изолирован как отдельный тенант: HEALTH_DATA_DIR + HEALTH_SECRETS_DIR
#    указывают в staging (снапшот + ФИКТИВНЫЕ секреты). Настоящие секреты владельца
#    в прогоне не участвуют → suite не может послать реальное сообщение/утечь секрет.
set -euo pipefail

STUDIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && /opt/homebrew/bin/python3.11 -c 'import infra_config; print(infra_config.STUDIO_SSH)')"   # адрес — private/infra.yaml
LOCAL_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANON_DB="$HOME/health/data/health.db"
REFERENCE_DB="$HOME/health_reference/loinc.db"
PY="/opt/homebrew/bin/python3.11"

# ── Каталог стенда: свой на КАЖДЫЙ запуск (2026-09-11) ───────────────────────
# Было: один жёсткий $HOME/health_staging на всех. Два одновременных
# запуска rsync'или в него по очереди, и второй перезаписывал дерево первого —
# первый получал зелёный на ЧУЖОМ коде, молча. Сколько раз это случалось, узнать
# нельзя: скрипт не логирует свои запуски, так что правка сделана по устройству,
# а не по замеренной частоте.
# Уникальность на запуск, а не на ветку: одну ветку могут проверять две нити.
STAGING_ROOT="$HOME/health_staging_runs"
STAGING=""          # заполняется после sanity — до неё в сеть не ходим
STAGING_SECRETS=""
# Уборка только СВОЕГО каталога — чужие прогоны не трогаем. Путь проверяется на
# принадлежность корню: пустая или неожиданная переменная не должна дать rm -rf /.
cleanup_staging() {
  case "$STAGING" in
    "$STAGING_ROOT"/run.*) ssh "$STUDIO" "rm -rf '$STAGING'" >/dev/null 2>&1 || true ;;
  esac
}
trap cleanup_staging EXIT

# Reference (loinc.db, 232 МБ) общий на все запуски: тесты только читают его, а
# копировать четверть гигабайта в каждый прогон — плата без выгоды. Обновление
# атомарное: cp во временный файл в том же каталоге + mv, иначе два одновременных
# прогона, одновременно увидевших устаревший файл, писали бы в него вдвоём.
SHARED_REF="$STAGING_ROOT/_reference_shared"

# Селектор. Пусто = полный набор. Непусто = ЧАСТИЧНЫЙ прогон (см. шапку).
TARGET="${1:-tests/}"
if [[ "$TARGET" == "tests/" ]]; then
  PARTIAL=0
else
  PARTIAL=1
fi

echo "== 1/4 sanity =="
if [[ "$(hostname)" == Studio* ]]; then
  echo "ОШИБКА: запускай с MacBook. На Studio это бессмысленно (и опасно)." >&2
  exit 1
fi
echo "  repo=$LOCAL_REPO"

STAGING="$(ssh "$STUDIO" "mkdir -p '$STAGING_ROOT' && mktemp -d '$STAGING_ROOT/run.XXXXXXXX'" 2>/dev/null || echo "")"
case "$STAGING" in
  "$STAGING_ROOT"/run.*) ;;
  *) echo "ОШИБКА: не удалось создать каталог стенда на Studio (получено: '$STAGING')" >&2
     # Урок C-146 (03.10): пустой ответ чаще всего значит, что Studio недоступна, — так было, когда
     # Tailscale на MacBook разлогинился, и очередь закрытий простояла ~6 часов. Называем причину.
     TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
     if [[ -x "$TS" ]] && "$TS" status 2>&1 | grep -qi "logged out"; then
       echo "  ПРИЧИНА: Tailscale на этой машине разлогинен — войдите в Tailscale и повторите." >&2
     elif ! ssh -o ConnectTimeout=8 "$STUDIO" true 2>/dev/null; then
       echo "  ПРИЧИНА: Studio ($STUDIO) не отвечает по ssh — проверьте, что она включена и в сети." >&2
     fi
     exit 1 ;;
esac
STAGING_SECRETS="$STAGING/.secrets_staging"
echo "  стенд=$STAGING (свой на этот запуск, убирается при выходе)"

echo "== 2/4 rsync WIP-дерева → Studio:$STAGING (без .git/logs/secrets/*.db; data/ репо едет, data/ тенанта защищён) =="
# СТОЛКНОВЕНИЕ ДВУХ data/ (2026-09-05): на staging корень репо = HEALTH_DATA_DIR, поэтому
# `data/` — и каталог тенанта (health.db, documents/, reports/), и с 2026-09-02 каталог
# КОДОВЫХ активов репо (data/norm_docs — снимки норм, data/prompts). Прежний `--exclude 'data/'`
# защищал первое и молча не довозил второе: safety_net падал на импорте (FileNotFoundError
# ctcae_lab_v6.0.json) и 4 файла тестов не собирались. Защищаем тенантские имена точечно;
# исключённое --delete не трогает.
rsync -az --delete \
  `# '.git' БЕЗ слэша (2026-09-13). В главной копии .git — КАТАЛОГ, и шаблон со` \
  `# слэшем его ловил. В дереве нити (§21) .git — ФАЙЛ с указателем gitdir на` \
  `# главную копию, и шаблон со слэшем его НЕ ловит: в песочницу уезжал висячий` \
  `# указатель, git внутри неё падал rc=128, и четыре теста краснели так, будто` \
  `# сломан код. Первый же полный прогон из дерева нити это и показал. Шаблон без` \
  `# слэша совпадает и с файлом, и с каталогом.` \
  --exclude '.git' \
  --exclude '/data/health.db*' \
  --exclude '/data/documents/' \
  --exclude '/data/reports/' \
  --exclude 'logs/' \
  --exclude '.health_secrets/' \
  --exclude '.secrets_staging/' \
  --exclude '__pycache__/' \
  --exclude '*.db' \
  --exclude '.venv/' \
  --filter=':- .gitignore' \
  "$LOCAL_REPO"/ "$STUDIO:$STAGING/"

# ФАКТЫ О GIT — БЕЗ САМОГО GIT (2026-08-10). `.git` в песочницу не едет и ехать не
# должен: репозиторий там означал бы, что оттуда можно коммитить и пушить. Но всё,
# что спрашивало git, в песочнице врало — пять doc-тестов падали `ls-files` rc=128,
# а снимок pass-set писал `head_sha: unknown`. Три «разные поломки», один корень.
# Манифест снимается в НАСТОЯЩЕМ репозитории и кладётся рядом с деревом.
"$PY" -c "import sys; sys.path.insert(0,'$LOCAL_REPO'); import git_facts; print('  манифест git:', git_facts.write_manifest())"
scp -q "$LOCAL_REPO/.git_facts.json" "$STUDIO:$STAGING/.git_facts.json"

echo "== 3/4 снапшот канона + фиктивные секреты → staging (всё остаётся на Studio) =="
ssh "$STUDIO" "
  set -e
  mkdir -p '$STAGING/data' '$STAGING_SECRETS'
  # Тенант в контейнере (метка RUNTIME): канон — живая база в томе, нативная — замороженная копия
  # на момент переезда (замер 30.09: стенд семь часов судил базу 06:34). Снимок — backup API
  # внутри контейнера (база в WAL, cp одного файла дал бы её без последних записей); контейнер
  # недоступен — отказ, не тихий откат на замороженную копию.
  if [ \"\$(cat ~/health/RUNTIME 2>/dev/null)\" = container ]; then
    export PATH=/opt/homebrew/bin:\$PATH
    docker --context colima-health exec health-cron-1 sh -c 'sqlite3 \"\$HEALTH_DATA_DIR/data/health.db\" \".backup /home/health/stand_snapshot.db\"'
    docker --context colima-health cp health-cron-1:/home/health/stand_snapshot.db '$STAGING/data/health.db'
    docker --context colima-health exec health-cron-1 rm -f /home/health/stand_snapshot.db
    echo \"  снимок из контейнера: events=\$(sqlite3 '$STAGING/data/health.db' 'SELECT count(*) FROM events')\"
  else
    cp '$CANON_DB' '$STAGING/data/health.db'
  fi
  chmod 600 '$STAGING/data/health.db'
  # Фиктивные секреты: гард secrets_dir доволен (HEALTH_SECRETS_DIR задан), но
  # реальные токены не в игре — реальный send/утечка невозможны.
  for f in anthropic_key healthcheck_url oura_token sync_token telegram_token; do
    printf 'STAGING_DUMMY' > '$STAGING_SECRETS/'\$f
  done
  printf '999999999' > '$STAGING_SECRETS/telegram_chat_id'
  printf '{}' > '$STAGING_SECRETS/google_calendar_token.json'
  chmod 600 '$STAGING_SECRETS'/* 2>/dev/null || true
  # Справочник LOINC живёт ОТДЕЛЬНЫМ общим файлом и в снапшот канона не попадает.
  # Без явного копирования тесты сопоставления шли бы на ПУСТОМ словаре и зеленели
  # бы, не проверив ничего (§12) — тот же класс, что git ls-files rc=128 в
  # периметре. Копируем и говорим размер вслух.
  # Копируем ТОЛЬКО если изменился: справочник перезаливается раз в полгода, а
  # прогон идёт по многу раз в день. Копия при каждом запуске была бы 232 МБ
  # переписывания ради данных, которые не менялись.
  # Кэш справочника ОБЩИЙ для всех прогонов (2026-09-11): каталог стенда теперь
  # свой на каждый запуск, и держать кэш внутри него значило бы копировать 232 МБ
  # каждый раз — ровно та плата, которую условие -nt и убирало.
  # Обновление атомарное: два одновременных прогона, увидевших устаревший файл,
  # без этого писали бы в него вдвоём и оставили бы обрезанную базу. Временное имя
  # несёт \$\$, mv в пределах одной ФС атомарен. В стенде — симлинк на общий файл:
  # тесты его только читают.
  mkdir -p '$SHARED_REF' '$STAGING/reference'
  if [ -f '$REFERENCE_DB' ]; then
    # Отсутствие кэша проверяется ОТДЕЛЬНО от -nt: удалённая команда идёт через
    # логин-шелл Studio, а там -nt против несуществующего файла дал false (первый
    # прогон 2026-09-11: «общий кэш свеж» при пустом каталоге — тесты LOINC пошли
    # бы на пустом словаре и зазеленели, не проверив ничего, §12).
    if [ ! -f '$SHARED_REF/loinc.db' ] || [ '$REFERENCE_DB' -nt '$SHARED_REF/loinc.db' ]; then
      cp '$REFERENCE_DB' '$SHARED_REF/loinc.db.tmp.'\$\$ && mv '$SHARED_REF/loinc.db.tmp.'\$\$ '$SHARED_REF/loinc.db'
      echo '  reference: обновлён в общем кэше, '\$(ls -lah '$SHARED_REF/loinc.db' | awk '{print \$5}')
    else
      echo '  reference: общий кэш свеж, '\$(ls -lah '$SHARED_REF/loinc.db' | awk '{print \$5}')
    fi
    ln -sf '$SHARED_REF/loinc.db' '$STAGING/reference/loinc.db'
  else
    echo '  ⚠ справочник $REFERENCE_DB не найден — тесты LOINC пойдут на пустом словаре'
  fi
  echo '  snapshot size: '\$(ls -lah '$STAGING/data/health.db' | awk '{print \$5}')
"

if [[ "$PARTIAL" -eq 1 ]]; then
  echo "== 4/5 pytest ЧАСТИЧНО: '$TARGET' (НЕ заменяет полный прогон) =="
else
  echo "== 4/5 pytest tests/ против снапшота (HEALTH_DATA_DIR + HEALTH_SECRETS_DIR = staging) =="
fi
# Вывод pytest дублируется в файл, чтобы число passed доехало до отметки о прогоне (28.09):
# раньше его видел только тот, кто смотрел в терминал, и в записки нитей оно шло «из вывода».
# Код возврата — через pipefail (стоит в шапке): tee возвращает 0, значит rc конвейера = rc ssh,
# то есть pytest. Отказ самого tee тоже даст rc≠0 — красный, а не ложно-зелёный.
PYTEST_OUT="$(mktemp -t pytest_out)"
set +e
ssh "$STUDIO" "cd '$STAGING' && HEALTH_DATA_DIR='$STAGING' HEALTH_SECRETS_DIR='$STAGING_SECRETS' HEALTH_REFERENCE_DIR='$STAGING/reference' $PY -m pytest $TARGET -q --tb=short -m 'not slow and not requires_anthropic_key and not owner_env'" | tee "$PYTEST_OUT"
RC=$?
set -e
PASSED="$(grep -Eo '[0-9]+ passed' "$PYTEST_OUT" | tail -1 | grep -Eo '[0-9]+' || true)"
rm -f "$PYTEST_OUT"

if [[ "$PARTIAL" -eq 1 ]]; then
  # Код-гард гоняет ВЕСЬ монитор — для подмножества тестов это не по адресу и
  # только маскировало бы, что прогон частичный.
  echo
  if [ "$RC" -eq 0 ]; then
    echo "🟡 ЧАСТИЧНЫЙ PASS — селектор '$TARGET'. Код-гард НЕ гонялся."
    echo "   Это НЕ вердикт о регрессии: полный прогон обязателен до коммита."
    exit 0
  else
    echo "❌ ЧАСТИЧНЫЙ FAIL (pytest rc=$RC) — селектор '$TARGET'. Канон не тронут."
    exit 1
  fi
fi

# Шаг 5 (2026-07-17): закрывает класс «сломанный check() в integrity_tests». pytest НЕ ловит его
# (монитор здесь не гоняется — он про живые данные; а check() глотает исключение → зелёно), баг
# всплывал лишь в ночном --json. Страж гоняет монитор на снапшоте и падает ТОЛЬКО на ошибках КОДА
# (NameError/typo/импорт/сигнатура), игнорируя data-FAIL/WARN (на суточном снапшоте они ложны).
echo "== 5/5 integrity code-guard: монитор на снапшоте, ТОЛЬКО ошибки КОДА (data-FAIL игнор) =="
set +e
ssh "$STUDIO" "cd '$STAGING' && HEALTH_DATA_DIR='$STAGING' HEALTH_SECRETS_DIR='$STAGING_SECRETS' HEALTH_REFERENCE_DIR='$STAGING/reference' $PY scripts/integrity_code_guard.py"
RC_GUARD=$?
set -e

echo
if [ "$RC" -eq 0 ] && [ "$RC_GUARD" -eq 0 ]; then
  echo "✅ PASS — регрессия кода зелёная + монитор без код-ошибок. Деплой-target и канон не тронуты."
  # Отметка о прогоне — единственное, что обнуляет возраст доказательства (Р-3).
  # Ставится ТОЛЬКО здесь: полный прогон + зелёный. Частичный говорит о подмножестве,
  # красный не подтверждение — ни тот, ни другой сюда не доходят по конструкции.
  # Читает `integrity_tests.check_suite_freshness` → ночной монитор → триаж.
  # Пишем в `logs/` (в .gitignore), поэтому деплой-target не становится грязным.
  # Версия записывается вместе с отметкой: §12 — зелёный без имени версии не вердикт.
  SHA="$(git -C "$LOCAL_REPO" rev-parse --short HEAD)"
  if ! git -C "$LOCAL_REPO" diff --quiet || ! git -C "$LOCAL_REPO" diff --cached --quiet; then
    SHA="$SHA+wip"
  fi
  ssh "$STUDIO" "mkdir -p ~/health_scripts/logs && printf '%s\n' \
    '{\"ran_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"commit\": \"$SHA\", \"full\": true, \"where\": \"staging\", \"passed\": ${PASSED:-null}}' \
    > ~/health_scripts/logs/suite_last_run.json"
  # Монитор владельца живёт в контейнере и читает отметку из СВОИХ logs/ (30.09) — туда же копия.
  ssh "$STUDIO" "[ \"\$(cat ~/health/RUNTIME 2>/dev/null)\" = container ] || exit 0
    export PATH=/opt/homebrew/bin:\$PATH
    docker --context colima-health cp ~/health_scripts/logs/suite_last_run.json health-cron-1:/app/logs/suite_last_run.json" \
    || echo "   ⚠ отметка в контейнер не доставлена — монитор владельца её не увидит"
  echo "   отметка о прогоне: logs/suite_last_run.json ($SHA, passed=${PASSED:-?})"
  exit 0
else
  echo "❌ FAIL (pytest rc=$RC, integrity-code rc=$RC_GUARD) — почини ДО коммита. Канон не тронут."
  exit 1
fi
