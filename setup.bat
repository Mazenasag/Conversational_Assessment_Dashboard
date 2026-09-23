@echo off
setlocal
cd /d "%~dp0"

set "BOOTSTRAP_PY="
where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PY=py -3"
if not defined BOOTSTRAP_PY (
  where python >nul 2>nul
  if not errorlevel 1 set "BOOTSTRAP_PY=python"
)
if not defined BOOTSTRAP_PY (
  echo ERROR: Python 3 was not found.
  echo Install Python 3.10 or newer from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv...
  %BOOTSTRAP_PY% -m venv .venv
  if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist ".env" if exist ".env.example" copy /Y ".env.example" ".env" >nul

echo.
echo Setup complete.
echo Edit .env and add GEMINI_API_KEY, then run run_dashboard.bat.
pause
exit /b 0

:error
echo.
echo ERROR: Setup failed.
pause
exit /b 1
