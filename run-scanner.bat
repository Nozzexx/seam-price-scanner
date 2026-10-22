@echo off
title S.E.A.M. Price Scanner
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up the scanner. This takes a minute.
  python -m venv .venv || (echo Python 3.10 or newer is required: https://www.python.org/downloads/ & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo Install failed. & pause & exit /b 1)
)
rem The window. Pass --console for the old text-only mode.
if /i "%~1"=="--console" (
  ".venv\Scripts\python.exe" seam_scanner.py %2 %3 %4 %5 %6 %7 %8 %9
  pause
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" seam_scanner_gui.py
