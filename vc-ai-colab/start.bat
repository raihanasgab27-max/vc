@echo off
echo ========================================
echo   VoiceAI - Starting...
echo ========================================
echo.

if not exist venv (
    echo [ERROR] Run setup.bat first!
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python app.py
if %errorlevel% neq 0 (
    echo.
    echo [TIP] If it says "ModuleNotFoundError", try running setup.bat again.
)
pause
