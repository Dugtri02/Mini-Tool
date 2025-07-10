@echo off
REM Launch the Mini-Tool Discord bot

REM Check Python version
python --version > pyver.txt 2>&1
findstr /R /C:"Python 3\.11\." pyver.txt >nul
if errorlevel 1 (
    echo.
    echo [ERROR] Python 3.11.x is required. Please install Python 3.11 and make sure it's in your PATH.
    echo Detected version:
    type pyver.txt
    del pyver.txt
    pause
    exit /b
)
del pyver.txt

REM Check if requirements are installed
python -c "import discord" 2>NUL
if errorlevel 1 (
    echo.
    echo [INFO] Installing required Python packages...
    pip install -r requirements.txt
)

REM Run the bot
python main.py
pause