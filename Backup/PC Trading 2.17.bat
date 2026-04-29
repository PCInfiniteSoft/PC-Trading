@echo off
if "%1"=="min" goto start_bot

:: สั่งเปิดตัวเองใหม่ในโหมด Minimized แล้วปิดหน้าต่างปกติทิ้งทันที
start /min cmd /c "%~f0" min
exit

:start_bot
:: เริ่มทำงานในโหมดที่ย่อหน้าต่างแล้ว
call .venv\Scripts\activate.bat
python gui_main.py