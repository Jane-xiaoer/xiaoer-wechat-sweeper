@echo off
chcp 65001 >nul
title 小耳微信清扫器
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没找到 Python。
  echo.
  echo   这个工具需要 Python 才能跑。去 https://www.python.org/downloads/
  echo   下载安装，安装时记得勾上 "Add Python to PATH"，然后再双击我。
  echo.
  pause
  exit /b 1
)

python panel.py
if errorlevel 1 (
  echo.
  echo   启动失败。把上面的报错发给小耳。
  echo.
  pause
)
