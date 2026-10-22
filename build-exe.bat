@echo off
rem Builds dist\SEAM Price Scanner\ (a folder with SEAM Price Scanner.exe) and a zip to share.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install -q pyinstaller==6.22.3 || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name "SEAM Price Scanner" --icon assets\seam.ico --version-file packaging\version-info.txt ^
  --add-data "assets;assets" --collect-submodules winrt ^
  seam_scanner_gui.py || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\SEAM Price Scanner' -DestinationPath 'dist\SEAM-Price-Scanner-1.3.0.zip'; (Get-FileHash 'dist\SEAM-Price-Scanner-1.3.0.zip' -Algorithm SHA256).Hash | Out-File -Encoding ascii 'dist\SEAM-Price-Scanner-1.3.0.zip.sha256'"
echo Built dist\SEAM-Price-Scanner-1.3.0.zip
