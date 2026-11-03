#!/bin/zsh
# Weekly ram review, run by launchd (com.agent-stack.ram.weekly). Leaves a report and pending proposals.
RAM="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$RAM/runs/weekly.log"
cd "$RAM" || exit 1
START=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
echo "=== $(date) ===" >> "$LOG"
"$(command -v claude || echo /opt/homebrew/bin/claude)" -p "weekly" --agent ram \
  --allowedTools "Bash Read Write Glob Grep Agent" --disallowedTools "Edit" >> "$LOG" 2>&1
rc=$?
# ram's own cost: everything recorded in the ram folder since this run started (lead + judges)
python3 "$RAM/scripts/ingest.py" > /dev/null
REPORT="$RAM/runs/$(date +%F)/report.md"
python3 "$RAM/scripts/selfcost.py" --since "$START" --report "$REPORT" --heading >> "$LOG" 2>&1
# rebuild the PDF so it includes ram's measured cost
python3 "$RAM/scripts/render_pdf.py" "$REPORT" --subtitle "ram weekly review, $(date -v-7d '+%-d %B') to $(date '+%-d %B %Y')" >> "$LOG" 2>&1
n=$(python3 "$RAM/scripts/changes.py" list --status pending 2>/dev/null | wc -l | tr -d ' ')
osascript -e "display notification \"Weekly review PDF ready. $n proposals pending. Run /ram review.\" with title \"ram\"" 2>/dev/null
open "${REPORT%.md}.pdf" 2>/dev/null
exit $rc
