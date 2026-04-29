import os

# รายชื่อ Library ที่บอทเราใช้ทั้งหมด
libraries = [
    "MetaTrader5",
    "openai",
    "cloudscraper",
    "customtkinter",
    "pillow",
    "requests",
    "python-dotenv"
]

# สร้างไฟล์ requirements.txt
with open("requirements.txt", "w", encoding="utf-8") as f:
    for lib in libraries:
        f.write(f"{lib}\n")

# สร้างไฟล์ Setup (Batch file สำหรับ Windows)
setup_script = """@echo off
echo ===========================================
echo   PC Trading Setup - New Computer
echo ===========================================
echo [1/3] Creating Virtual Environment...
python -m venv .venv

echo [2/3] Activating Virtual Environment...
call .venv\\Scripts\\activate

echo [3/3] Installing Libraries...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ===========================================
echo SETUP COMPLETE! You can now run your bot.
pause
"""

with open("install_bot.bat", "w", encoding="utf-8") as f:
    f.write(setup_script)

print("✅ สร้างไฟล์ requirements.txt และ install_bot.bat เรียบร้อยครับพี่โก้!")