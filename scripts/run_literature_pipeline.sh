#!/bin/bash
# Запуск reader + curator. Reader сначала, curator после.
# Каталог — от самого скрипта, интерпретатор — шов HEALTH_PY (как run_checks.sh): в образе
# нет ни $HOME/health_scripts, ни /opt/homebrew (код в /app, HEALTH_PY=python3).
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1
PY="${HEALTH_PY:-/opt/homebrew/bin/python3.11}"
"$PY" publication_reader.py --max 10 2>&1
"$PY" literature_curator.py --max 10 2>&1
