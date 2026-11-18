@echo off
REM Quick check (~5-10 min). Writes nothing to data\.
cd /d %~dp0
call .venv\Scripts\activate
set PYTHONPATH=src
python -m sfjobs.selftest %*
pause
