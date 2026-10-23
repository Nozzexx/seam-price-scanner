@echo off
rem Builds dist\SEAM Price Scanner\ (the app folder), then two downloads with SHA-256 files:
rem   dist\SEAM-Price-Scanner-Setup.exe   installer (Inno Setup 6; skipped if ISCC is not found)
rem   dist\SEAM-Price-Scanner-<ver>.zip   portable copy
rem The version comes from VERSION in seam_scanner.py.
cd /d "%~dp0"
for /f "usebackq delims=" %%v in (`powershell -NoProfile -Command "(Select-String -Path seam_scanner.py -Pattern '^VERSION = ').Line.Split([char]34)[1]"`) do set VER=%%v
if "%VER%"=="" (echo Could not read VERSION from seam_scanner.py & exit /b 1)
echo Building S.E.A.M. Price Scanner %VER%
".venv\Scripts\python.exe" -m pip install -q pyinstaller==6.22.3 || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name "SEAM Price Scanner" --icon assets\seam.ico --version-file packaging\version-info.txt ^
  --add-data "assets;assets" --collect-submodules winrt ^
  seam_scanner_gui.py || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\SEAM Price Scanner' -DestinationPath 'dist\SEAM-Price-Scanner-%VER%.zip'; (Get-FileHash 'dist\SEAM-Price-Scanner-%VER%.zip' -Algorithm SHA256).Hash | Out-File -Encoding ascii 'dist\SEAM-Price-Scanner-%VER%.zip.sha256'" || exit /b 1

set ISCC=
for %%p in ("%ISCC_PATH%" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%p if not defined ISCC set ISCC=%%p
if not defined ISCC (echo Inno Setup 6 not found: built the zip only. & exit /b 0)
%ISCC% /Q /DAppVersion=%VER% packaging\installer.iss || exit /b 1
powershell -NoProfile -Command "(Get-FileHash 'dist\SEAM-Price-Scanner-Setup.exe' -Algorithm SHA256).Hash | Out-File -Encoding ascii 'dist\SEAM-Price-Scanner-Setup.exe.sha256'" || exit /b 1
echo Built dist\SEAM-Price-Scanner-Setup.exe and dist\SEAM-Price-Scanner-%VER%.zip
