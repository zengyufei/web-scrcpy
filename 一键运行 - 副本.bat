@echo off
chcp 65001 >nul
echo [3/3] 正在启动域名自动化切换工具...
call venv\Scripts\activate
python app.py
pause