@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo Conversational Assessment - LOCAL PROCESSOR
echo ==========================================
echo.

set "APP_MODE=local"
set "PYTHON_EXE="

REM 1) Prefer a .venv inside this project
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
)

REM 2) Fall back to the user's existing working dashboard environment
if not defined PYTHON_EXE if exist "C:\Users\User\Downloads\Conversational_Assessment_Dashboard\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=C:\Users\User\Downloads\Conversational_Assessment_Dashboard\.venv\Scripts\python.exe"
)

REM 3) Try Windows Python launcher
if not defined PYTHON_EXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=py"
)

REM 4) Try python on PATH
if not defined PYTHON_EXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    echo ERROR: Python could not be found.
    echo.
    echo Expected one of:
    echo   .venv\Scripts\python.exe
    echo   C:\Users\User\Downloads\Conversational_Assessment_Dashboard\.venv\Scripts\python.exe
    echo   py
    echo   python
    echo.
    pause
    exit /b 1
)

if not exist "app.py" (
    echo ERROR: app.py was not found in:
    echo   %CD%
    echo.
    echo Put this BAT file in the project root beside app.py.
    echo.
    pause
    exit /b 1
)

echo Python:
echo   %PYTHON_EXE%
echo.
echo Project:
echo   %CD%
echo.
echo APP_MODE=local
echo.
echo Starting Streamlit...
echo The browser should open automatically.
echo If not, open:
echo   http://localhost:8501
echo.

"%PYTHON_EXE%" -m streamlit run app.py --server.port 8501

echo.
echo ==========================================
echo Streamlit stopped or failed to start.
echo Review any error shown above.
echo ==========================================
pause
