@echo off
chcp 65001 >nul
rem ============================================================
rem  TotemFix 开发/源码运行入口（优先使用已打包的 exe）
rem ============================================================
cd /d "%~dp0"

if exist "%~dp0TotemFix.exe" (
    start "" "%~dp0TotemFix.exe" %*
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m errordoctor %*
    exit /b 0
)

echo [TotemFix] 未找到 Python。请运行 scripts\build.bat 打包 exe，或安装 Python 3.8+。
pause
