@echo off
chcp 65001 >nul
rem ============================================================
rem  一键打包 TotemFix 为单文件 TotemFix.exe
rem  需在 Windows 上运行，并已安装 Python 3.8+
rem ============================================================
cd /d "%~dp0.."

echo [1/2] 安装 PyInstaller ...
python -m pip install --upgrade pyinstaller || goto :fail

echo [2/2] 打包中（约 1~3 分钟）...
python -m PyInstaller --clean --noconfirm scripts\TotemFix.spec || goto :fail

echo.
echo 打包完成！dist\TotemFix.exe
echo 把该 exe 复制到 PCL 文件夹（与 PCL.exe 同级）双击即可使用。
pause
exit /b 0

:fail
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
