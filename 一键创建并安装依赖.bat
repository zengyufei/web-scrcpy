@echo off
chcp 65001 >nul

echo 正在创建环境...
call python -m venv venv
echo 正在安装依赖...
call venv\Scripts\activate
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
echo 部署完成！
pause