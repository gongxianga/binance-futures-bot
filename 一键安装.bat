@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 币安合约交易软件 - 安装程序

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║      币安合约交易软件  v2.0  安装程序      ║
echo  ╚══════════════════════════════════════════╝
echo.

set "INSTALL_DIR=%~dp0"
set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

:: ── 检测 Python ───────────────────────────────
echo  [1/4]  检测 Python 环境...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
    echo        已找到 Python !PY_VER!
    set "PYTHON_CMD=python"
    goto :install_deps
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('py --version 2^>^&1') do set "PY_VER=%%v"
    echo        已找到 Python !PY_VER!
    set "PYTHON_CMD=py"
    goto :install_deps
)

:: ── 下载并安装 Python ─────────────────────────
echo        未检测到 Python，准备自动下载安装...
echo.
echo  [*]   正在下载 Python 3.12（约 25MB）...

set "PY_INSTALLER=%TEMP%\python_installer.exe"
powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe', '%PY_INSTALLER%') }" 2>nul
if not exist "%PY_INSTALLER%" (
    echo.
    echo  [错误] 下载失败，请手动安装 Python: https://www.python.org/downloads/
    pause & exit /b 1
)

echo  [*]   正在安装 Python...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
del "%PY_INSTALLER%" >nul 2>&1
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
set "PYTHON_CMD=python"
echo        Python 安装完成！

:install_deps
echo.
echo  [2/4]  安装依赖库（python-binance / matplotlib / numpy）...
%PYTHON_CMD% -m pip install --upgrade pip -q
%PYTHON_CMD% -m pip install python-binance matplotlib numpy -q
if %errorlevel% neq 0 (
    echo  [错误] 依赖安装失败，请检查网络后重试。
    pause & exit /b 1
)
echo        依赖安装完成！

:: ── 创建启动脚本 ──────────────────────────────
echo.
echo  [3/4]  创建启动文件...
set "LAUNCHER=%INSTALL_DIR%\启动交易软件.bat"
(
    echo @echo off
    echo chcp 65001 ^>nul
    echo cd /d "%INSTALL_DIR%"
    echo %PYTHON_CMD% "%INSTALL_DIR%\main.py"
    echo if %%errorlevel%% neq 0 pause
) > "%LAUNCHER%"

:: ── 创建桌面快捷方式 ──────────────────────────
echo  [4/4]  创建桌面快捷方式...
set "SHORTCUT=%USERPROFILE%\Desktop\币安合约交易软件.lnk"
powershell -Command "& { $s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%'); $s.TargetPath='%LAUNCHER%'; $s.WorkingDirectory='%INSTALL_DIR%'; $s.Description='币安合约交易软件 v2.0'; $s.Save() }" 2>nul
if exist "%SHORTCUT%" (echo        桌面快捷方式已创建！) else (echo        请直接双击"启动交易软件.bat"运行)

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║  安装完成！双击桌面"币安合约交易软件"启动  ║
echo  ╚══════════════════════════════════════════╝
echo.
set /p "LAUNCH=  是否立即启动？(Y/N): "
if /i "!LAUNCH!"=="Y" start "" "%LAUNCHER%"
echo.
pause
