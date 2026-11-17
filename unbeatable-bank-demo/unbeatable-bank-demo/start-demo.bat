@echo off
rem Serves the offline demo on http://localhost:8000 and opens it.
cd /d "%~dp0app"
start "" http://localhost:8000
echo Unbeatable Bank demo at http://localhost:8000  (close this window to stop)
py -3 -m http.server 8000 2>nul || python -m http.server 8000 || (echo Python 3 is needed. Install it from python.org, or run: npx serve app & pause)
