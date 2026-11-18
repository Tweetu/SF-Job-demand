@echo off
REM One-time local setup (Windows). Needs Python 3.10+ from python.org ("Add to PATH" ticked).
cd /d %~dp0
python -m venv .venv || (echo Python not found - install from https://www.python.org/downloads/ & pause & exit /b 1)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
REM Windows uses the built-in Microsoft Edge, so no browser download is needed.
echo.
echo Setup done. Next: double-click selftest.bat
pause
