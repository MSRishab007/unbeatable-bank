#!/bin/sh
# Serves the offline demo on http://localhost:8000 and opens it.
cd "$(dirname "$0")/app" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python 3 is needed. Or run: npx serve app"; exit 1; }
URL=http://localhost:8000
( sleep 1; (command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") ) >/dev/null 2>&1 &
echo "Unbeatable Bank demo at $URL  (Ctrl+C to stop)"
exec "$PY" -m http.server 8000
