@echo off
rem Arranca el bot. Si se cae, vuelve a arrancarlo a los 10 segundos.
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" -m bot
echo El bot se ha parado. Reinicio en 10 segundos (Ctrl+C para salir)...
timeout /t 10 /nobreak >nul
goto loop
