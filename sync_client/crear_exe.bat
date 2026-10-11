@echo off
rem Genera dist\sincronizador.exe para los oficiales (no necesitan instalar Python).
cd /d "%~dp0"
"..\.venv\Scripts\python.exe" -m pip install -q pyinstaller requests lupa
"..\.venv\Scripts\python.exe" -m PyInstaller --onefile --console --name sincronizador --collect-all lupa --paths . sincronizador.py
echo.
echo Listo: %~dp0dist\sincronizador.exe
pause
