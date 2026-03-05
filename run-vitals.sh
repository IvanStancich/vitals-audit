#!/bin/bash
# Run vitals check. Expects /tmp/vitals-cron-state.json to already exist.
# Usage: bash run-vitals.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATE=$(TZ=Europe/Rome date +%Y-%m-%d)
AUDITS_DIR="$HOME/.openclaw/workspace/memory/audits"

mkdir -p "$AUDITS_DIR"

# Run the vitals check
OUTPUT=$("$SCRIPT_DIR/vitals-check.py" 2>/dev/null)
EXIT_CODE=$?

# Save raw JSON
echo "$OUTPUT" > "$AUDITS_DIR/${DATE}-vitals.json"

# Print output for the agent to interpret
echo "$OUTPUT"

exit $EXIT_CODE
