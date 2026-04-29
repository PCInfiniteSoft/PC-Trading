@echo off
echo 🚀 [PC Trading 2.0] Starting Library Installation...
echo ----------------------------------------------------

:: 1. ตรวจสอบและสร้าง .venv หากยังไม่มี
if not exist ".venv" (
    echo 📦 Creating Virtual Environment .venv ...
    python -m venv .venv
)

:: 2. มุดเข้ากระบะทราย (Activate .venv)
echo 🔌 Activating Virtual Environment...
call .venv\Scripts\activate.bat

:: 3. อัปเกรด pip ให้เป็นเวอร์ชันล่าสุด
echo 🛠️ Upgrading pip...
python -m pip install --upgrade pip

:: 4. ติดตั้ง Library ทั้งหมดที่บอทต้องใช้
echo 📚 Installing required libraries...
python -m pip install requests customtkinter MetaTrader5 pandas pandas_ta discord.py google-genai pygetwindow cloudscraper

echo ----------------------------------------------------
echo ✅ All libraries installed successfully!
echo 💡 You can now run your bot using Debug_Mode.bat or PC Trading 2.0.bat
echo ----------------------------------------------------
pause