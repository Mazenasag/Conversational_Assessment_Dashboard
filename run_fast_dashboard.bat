@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
  where py >nul 2>nul
  if not errorlevel 1 set "PYTHON_EXE=py"
)
if not defined PYTHON_EXE (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE (
  echo ERROR: Python was not found.
  echo Install Python 3.10 or newer and try again.
  pause
  exit /b 1
)

if not exist "frontend\index.html" (
  echo ERROR: frontend\index.html was not found.
  pause
  exit /b 1
)

start "Conversational Assessment Static Server" /min cmd /c ""%PYTHON_EXE%" -m http.server 8080 -d frontend"
timeout /t 2 /nobreak >nul
start "" "http://localhost:8080/"
exit /b 0
