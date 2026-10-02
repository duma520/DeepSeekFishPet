@echo off
rem ============================================================
rem  DeepSeek Fish Pet - run from source (development)
rem  Works no matter what the current directory is.
rem  NOTE: keep this file ASCII-only, CRLF, and WITHOUT BOM.
rem ============================================================
cd /d "%~dp0"

set "PY=D:\Program Files\Python310\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0DeepSeekFishPet.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [DeepSeekFishPet] exit code = %RC%
    pause
)
