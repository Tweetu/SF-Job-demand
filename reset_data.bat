@echo off
REM Clears local test results so GitHub starts with clean history.
cd /d %~dp0
del /q data\state\*.csv data\state\*.gz data\metrics\*.csv reports\*.md reports\*.html reports\*.json 2>nul
echo Test data cleared.
pause
