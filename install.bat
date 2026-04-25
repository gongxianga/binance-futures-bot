@echo off
chcp 65001 >nul
echo 正在安装依赖...
pip install -r requirements.txt
echo.
echo 安装完成！请运行 run.bat 启动软件。
pause
