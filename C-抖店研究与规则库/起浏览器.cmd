@echo off
chcp 65001 >nul
setlocal
title 抖店送货 · 浏览器(9224)

set EXE=C:\Program Files\Citro Labs\ego lite\Application\ego.exe
set UD=F:\ego-ud-link

echo ========================================================
echo  启动带调试端口的 ego（独立 profile，不动你日常那个窗口）
echo  端口 9224    profile %UD%
echo.
echo  起来之后：
echo    1. 在这个窗口里打开 https://fxg.jinritemai.com  并登录一次
echo    2. 登录完保持窗口开着，回主会话说一声
echo ========================================================
echo.

if not exist "%EXE%" (
  echo [ERR] 找不到 ego：%EXE%
  pause
  exit /b 1
)
if not exist "%UD%" (
  echo [ERR] 找不到 profile：%UD%
  pause
  exit /b 1
)

rem 先看 9224 是不是已经有浏览器在跑
curl -s -m 2 http://127.0.0.1:9224/json/list >nul 2>&1
if %errorlevel%==0 (
  echo [OK] 9224 已经有浏览器在跑，不用再启动。
  echo      直接跑： node 抖店研究\engine\dd.mjs cats
  timeout /t 6 >nul
  exit /b 0
)

echo [..] 正在启动 ego ...
start "" "%EXE%" --remote-debugging-port=9224 --user-data-dir="%UD%" https://fxg.jinritemai.com/ffa/g/list

echo [..] 等待端口就绪 ...
for /l %%i in (1,1,30) do (
  curl -s -m 2 http://127.0.0.1:9224/json/list >nul 2>&1
  if not errorlevel 1 (
    echo.
    echo [OK] 9224 已就绪（等了 %%i 秒）
    goto ready
  )
  timeout /t 1 >nul
)
echo.
echo [ERR] 30 秒内没起来。
echo       常见原因：%UD% 被另一个 ego 进程占用（先全部关掉 ego 再试）
pause
exit /b 1

:ready
echo.
echo 下一步： node 抖店研究\engine\dd.mjs cats
timeout /t 8 >nul
