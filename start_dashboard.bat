@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=python"

rem Prefer the project's .venv Python if available
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    set "PYTHON=%VENV_PY%"
)

rem If PYTHON points to an existing file, use it; otherwise use 'where' to resolve the command
if exist "%PYTHON%" (
    rem PYTHON is a file path that exists; proceed
) else (
    where %PYTHON% >nul 2>nul
    if errorlevel 1 (
        set "PYTHON=py"
        where %PYTHON% >nul 2>nul
        if errorlevel 1 (
            echo Python non trovato nel PATH. Installa Python oppure avvia start_dashboard.py manualmente.
            exit /b 1
        )
    )
)

"%PYTHON%" "%SCRIPT_DIR%start_dashboard.py" %*
exit /b %errorlevel%
