@echo off
chcp 65001 >nul
echo ========================================
echo   PT RSS 工具箱
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

:: 检查虚拟环境
if not exist "venv" (
    echo [*] 首次运行，创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

:: 激活虚拟环境
call venv\Scripts\activate.bat

:: 安装依赖
echo [*] 检查依赖...
pip show flask >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] 安装依赖...
    pip install -r requirements.txt
    echo.
    echo [*] 安装 Playwright 浏览器...
    playwright install chromium
)

:: 检查 .env 文件
if not exist ".env" (
    echo [警告] 未找到 .env 配置文件
    echo 请复制 .env.example 为 .env 并填写配置
    echo.
    pause
)

:: 启动服务
echo.
echo ========================================
echo   启动 Web 服务...
echo   访问地址: http://localhost:5000
echo ========================================
echo.
python main.py

pause
