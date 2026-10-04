#!/bin/bash
# Машинный судья Studio (нить machine-judge, 03.10): проверки самой машины — снимки MacBook,
# launchd, счётчик тенантов, SEC-датчики хоста — без привязки к тенанту.
#
# Зачем отдельно. После переезда владельца в контейнер (30.09) их судил только ночной прогон
# партнёра на хосте — случайно. Здесь — свой прогон: integrity_tests.py --scope machine.
# Данные тенантов он не читает; каталог данных — свой, вне ~/health* (обходчик тенантов его
# не видит). Результат и копия живого плиста кладутся в logs/ репозитория хоста; контейнер
# владельца читает их через HEALTH_HOST_LOGS (check_machine_judge_alive) и переносит находки
# себе — доставка та же, что у остальных проверок.
#
# Запуск: launchd com.larry.health.machine-check (07:40, до монитора владельца в 07:50).
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1
PY="${HEALTH_PY:-/opt/homebrew/bin/python3.11}"
LABEL="com.larry.health.machine-check"
OUT="logs/machine_integrity_latest.json"
LIVE="logs/launchd_live"
export HEALTH_DATA_DIR="${HEALTH_MACHINE_DATA_DIR:-$HOME/.health_machine}"
mkdir -p "$HEALTH_DATA_DIR" logs "$LIVE"

RAW="$(mktemp -t machine_check.XXXXXX)"
ERR="$(mktemp -t machine_check_err.XXXXXX)"
"$PY" integrity_tests.py --json --scope machine >"$RAW" 2>"$ERR"
# Результат пишется всегда: упавший прогон — тоже результат («broken»), иначе контейнер увидел бы
# вчерашний зелёный. Запись атомарная (tmp + mv): читатель не застанет половину файла.
"$PY" - "$RAW" "$ERR" "$OUT" <<'PYEOF'
import json, sys, os
from datetime import datetime
raw, err, out = sys.argv[1:4]
now = datetime.now().astimezone().isoformat(timespec="seconds")
try:
    data = json.loads(open(raw, encoding="utf-8").read())
except ValueError:
    tail = open(err, encoding="utf-8", errors="replace").read()[-600:]
    data = {"broken": tail or "пустой вывод integrity_tests --scope machine"}
data["ran_at"] = now
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, out)
PYEOF
rm -f "$RAW" "$ERR"
# Копия ЖИВОГО плиста рядом с результатом: ритм для проверки живости читатель берёт отсюда,
# а не из второго дома расписания.
cp -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$LIVE/$LABEL.plist" 2>/dev/null || true
