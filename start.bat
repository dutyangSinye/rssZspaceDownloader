@echo off
chcp 65001 >nul

echo ========================================
echo   rss-downloader 多租户版
echo ========================================

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

:: Create virtualenv if needed
if not exist "venv" (
    echo [*] 首次运行，创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

:: Install dependencies if Flask is missing
pip show flask >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] 安装依赖...
    pip install -r requirements.txt
)

:: Check env file
if not exist ".env" (
    echo [提示] 未找到 .env，建议先复制 .env.example 到 .env
)

echo.
echo ========================================
echo   启动下载器主服务 (multi-tenant)
echo   访问地址: http://localhost:5000
echo ========================================
echo.

python main.py

pause
