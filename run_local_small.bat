@echo off
REM Small real run (~20-30 min): 25 companies + portals for 10 min. Produces reports\dashboard.html
cd /d %~dp0
call .venv\Scripts\activate
set PYTHONPATH=src
python -m sfjobs.pipeline --max-discovery 25 --portal-budget 10
start "" reports\dashboard.html
type reports\summary.md
echo.
echo IMPORTANT: run reset_data.bat before uploading to GitHub (so test data does not count as week 1).
pause
