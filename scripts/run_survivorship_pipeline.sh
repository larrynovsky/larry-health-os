#!/bin/bash
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

# ── Lifecycle очереди proposals (ЕЖЕНЕДЕЛЬНО, независимо от biweekly-анализа) ──
# Системный дренаж (2026-07-04, BL-ALERT-TREND follow-up): pending старше 60д → expired.
# supersede-at-generation (save_problem_proposal) ловит GP-дубли сразу; expiry досушивает
# literature-advisory (problem_id=null) и заброшенные. Обратимо. Убирает вечный «pending >21д».
/opt/homebrew/bin/python3.11 -c "import health_db, proposals_db; n=proposals_db.expire_aged_proposals(60); print(f'{__import__(\"datetime\").datetime.now()}: expiry — {n} aged proposals → expired')" 2>&1

# ── biweekly survivorship-анализ (только чётные недели) ──
WEEK=$(date +%V)
PARITY=$((WEEK % 2))
if [ $PARITY -ne 0 ]; then
  echo "$(date): odd week ($WEEK), skipping survivorship analysis"
  exit 0
fi
/opt/homebrew/bin/python3.11 survivorship_analyzer.py 2>&1
/opt/homebrew/bin/python3.11 survivorship_curator.py --max 10 2>&1
