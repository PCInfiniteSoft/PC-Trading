@echo off
echo ===========================================
echo   PC Trading Setup - New Computer
echo ===========================================
echo [1/3] Creating Virtual Environment...
python -m venv .venv

echo [2/3] Activating Virtual Environment...
call .venv\Scripts\activate

echo [3/3] Installing Libraries...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ===========================================
echo SETUP COMPLETE! You can now run your bot.
pause
