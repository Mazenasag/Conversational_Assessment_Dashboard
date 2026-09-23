@echo off
setlocal
cd /d "%~dp0"

set "APP_MODE=local"
set "VENV_PY=.venv\Scripts\python.exe"
set "BOOTSTRAP_PY="

echo ==========================================
echo Conversational Assessment - LOCAL PROCESSOR
echo ==========================================
echo.

if exist "%VENV_PY%" goto :run

where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PY=py -3"
if not defined BOOTSTRAP_PY (
    where python >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PY=python"
)

if not defined BOOTSTRAP_PY (
    echo ERROR: Python 3 was not found.
    echo Install Python 3.10 or newer, then run this file again.
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo First run: creating project virtual environment...
%BOOTSTRAP_PY% -m venv .venv
if errorlevel 1 goto :setup_error

if not exist "%VENV_PY%" goto :setup_error

echo Installing project dependencies...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :setup_error
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :setup_error

:run
if not exist "app.py" (
    echo ERROR: app.py was not found beside this launcher.
    pause
    exit /b 1
)

if not exist ".env" if exist ".env.example" (
    echo NOTE: .env was not found.
    echo Copy .env.example to .env and add GEMINI_API_KEY before processing uncached data.
    echo.
)

echo Project: %CD%
echo Python:  %VENV_PY%
echo.
echo Starting Streamlit at http://localhost:8501
"%VENV_PY%" -m streamlit run app.py --server.port 8501

echo.
echo Streamlit stopped or failed to start.
pause
exit /b 0

:setup_error
echo.
echo ERROR: Automatic setup failed.
echo Check your Python installation and internet connection, then try again.
pause
exit /b 1
