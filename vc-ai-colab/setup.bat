@echo off
echo ========================================
echo   VoiceAI - Setup
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python launcher not found. Install Python 3.10 first.
    echo https://www.python.org/downloads/release/python-31011/
    pause
    exit /b 1
)

py -3.10 --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.10 not found.
    echo Install from: https://www.python.org/downloads/release/python-31011/
    echo Make sure to check "Add to PATH" during installation.
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
py -3.10 -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
)

echo [2/3] Activating venv and upgrading pip...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo [3/3] Installing dependencies...
echo (This may take a while, especially for rvc-python)
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Some dependencies failed to install.
    echo If you see an error about "Microsoft Visual C++", you need to install:
    echo https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
)

echo.
echo ========================================
echo   Setup complete!
echo   Run start.bat to launch the app.
echo ========================================
pause
