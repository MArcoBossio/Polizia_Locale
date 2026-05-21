@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=python"

where %PYTHON% >nul 2>nul
if errorlevel 1 (
    set "PYTHON=py"
    where %PYTHON% >nul 2>nul
    if errorlevel 1 (
        echo Python non trovato nel PATH. Installa Python oppure avvia start_dashboard.py manualmente.
        exit /b 1
    )
)

"%PYTHON%" "%SCRIPT_DIR%start_dashboard.py" %*
exit /b %errorlevel%
