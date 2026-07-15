#!/usr/bin/env bash
#
# Start the CROSSUB voice agent (Python worker) AND the browser tester (Next.js)
# together in one terminal. Press Ctrl+C once to stop both.
#
set -uo pipefail
cd "$(dirname "$0")"

# First run: install the web tester's deps if they're missing.
if [ ! -d web/node_modules ]; then
  echo "→ Installing web tester dependencies (first run only)…"
  ( cd web && npm install )
fi

# Stop both child processes when this script exits (including on Ctrl+C).
trap 'echo; echo "→ Stopping agent + tester…"; kill 0' EXIT

echo "→ Starting CROSSUB voice agent + browser tester…"
echo "   Open the tester at:  http://localhost:3000"
echo "   (Ctrl+C stops both.)"
echo

uv run crossub-voice-agent dev &
( cd web && npm run dev ) &
wait
