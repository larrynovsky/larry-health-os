#!/bin/bash
# Прогон затронутых тестов со СТРУКТУРНЫМ вердиктом.
#
# ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ. Прежде этот прогон жил внутри pre-commit и судился
# одним признаком — кодом возврата pytest. Код возврата не различает три разных
# события: «тесты красные», «часть файлов не собралась» и «прогон не состоялся».
# 12.09 это стоило дорого: один файл из 23 не собрался, pytest оборвал ВЕСЬ
# прогон (так он устроен: ошибка сборки прекращает сессию), хук напечатал
# «ЗАТРОНУТЫЕ ТЕСТЫ КРАСНЫЕ» — и выглядел сработавшим датчиком, хотя проверено
# было ноль. Замер в тот день: `pytest --collect-only tests/` давал 29 ошибок
# сборки, то есть попадание в эту яму было не редкостью, а бытом.
#
# Врущий датчик хуже молчащего: молчащий оставляет знание о незнании, врущий
# за неделю отучает смотреть. Поэтому вердикт стал наблюдаемой величиной, а
# наблюдаемая величина требует границы, у которой её можно замерить, — вот эта
# граница. Оракул: tests/unit/test_run_affected_tests.py.
#
# Вход:  пути к тестам аргументами.
# Выход: одна машиночитаемая строка в stdout —
#          AFFECTED total=N passed=N failed=N errors=N
#        подробности прогона — в файл из AFFECTED_LOG (по умолчанию /tmp).
# Код:   0 всё зелёное · 1 есть упавшие · 2 есть несобравшиеся файлы
#        · 3 прогон не состоялся (вердикт снять не удалось)
#
# Приоритет 2 над 1 намеренный: «часть не проверена» — худшая новость, чем
# «часть красная». Красное видно, непроверенное невидимо.
set -uo pipefail

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
LOG="${AFFECTED_LOG:-/tmp/affected_precommit.log}"
XML="${AFFECTED_XML:-/tmp/affected_precommit.xml}"

[ $# -gt 0 ] || { echo "AFFECTED total=0 passed=0 failed=0 errors=0"; exit 0; }

rm -f "$XML"

# --continue-on-collection-errors — ядро правки: без него первый несобравшийся
# файл отменяет прогон остальных, и вердикт относится не к тому набору.
# Окружение git снимается здесь же: хук — источник заражения, не тесты
# (инцидент a251b93, 12.09). Список равен списку фикстуры _no_inherited_git_env.
(
  unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX \
        GIT_OBJECT_DIRECTORY GIT_NAMESPACE GIT_ALTERNATE_OBJECT_DIRECTORIES \
        GIT_QUARANTINE_PATH \
        GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
        GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE GIT_EDITOR
  "$PYTHON" -m pytest "$@" -q -p no:cacheprovider \
      --continue-on-collection-errors --junit-xml="$XML"
) > "$LOG" 2>&1

# Числа берём из junit-xml, который пишет сам pytest, а не из разбора его
# текста: текст сводки меняется между версиями, атрибуты — контракт формата.
# Здесь это же и разводит «упало» (failures) и «не собралось» (errors) —
# различение, ради которого всё затевалось.
VERDICT=$("$PYTHON" - "$XML" <<'PY'
import sys, xml.etree.ElementTree as ET
try:
    root = ET.parse(sys.argv[1]).getroot()
    s = root if root.tag == "testsuite" else root.find("testsuite")
    if s is None:
        raise ValueError("нет testsuite")
    t = int(s.get("tests", 0)); e = int(s.get("errors", 0))
    f = int(s.get("failures", 0)); sk = int(s.get("skipped", 0))
    print(f"AFFECTED total={t} passed={t - e - f - sk} failed={f} errors={e}")
except Exception as exc:                      # noqa: BLE001 — причина уходит в stdout
    print(f"AFFECTED_BROKEN {type(exc).__name__}: {exc}")
PY
)

echo "$VERDICT"

case "$VERDICT" in
  AFFECTED_BROKEN*) exit 3 ;;
esac

ERRORS=$(echo "$VERDICT" | sed -n 's/.*errors=\([0-9]*\).*/\1/p')
FAILED=$(echo "$VERDICT" | sed -n 's/.*failed=\([0-9]*\).*/\1/p')

[ "${ERRORS:-0}" -gt 0 ] && exit 2
[ "${FAILED:-0}" -gt 0 ] && exit 1
exit 0
