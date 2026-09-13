@echo off
rem RepoLens double-click launcher (Windows).
rem Usage:
rem   start_repolens.bat                     -> repo taken from repolens.ini (repo=...) or %1
rem   start_repolens.bat D:\path\to\repo     -> analyze given repo root
rem Behavior:
rem   - 端口 8788 空闲  -> 启动服务，服务自身会在 0.8s 后自动打开浏览器页面
rem   - 端口 8788 被占用 -> 视为已有服务在运行，直接打开页面，不再重复启动
rem NOTE: keep this file ASCII-only to avoid codepage issues.

setlocal
cd /d "%~dp0"

rem NOTE: %PYEXE% must stay UNQUOTED on the command lines below: its value can be
rem       "py -3" (command + argument). Quoting it would make cmd.exe look for a
rem       program literally named "py -3" and fail (real bug found 2026-09-12).
set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
    python --version >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo [RepoLens] Python 3.10+ not found in PATH. Please install Python first.
    pause
    exit /b 1
)

rem --- resolve repo: %1 overrides repolens.ini ---
set "REPO="
if not "%~1"=="" set "REPO=%~1"
if not defined REPO (
    if exist repolens.ini (
        for /f "usebackq eol=# tokens=1,* delims==" %%a in (`findstr /i "^repo=" repolens.ini`) do set "REPO=%%b"
    )
)
if not defined REPO (
    echo [RepoLens] 未指定仓库：请在 repolens.ini 设置 repo= 或传入路径参数。
    pause
    exit /b 1
)

rem --- port check: 8788 占用则视为已有服务在跑，直接打开页面，不重复启动 ---
set "PORT=8788"
%PYEXE% -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',8788))==0 else 1)" >nul 2>nul
if errorlevel 1 (
    echo [RepoLens] starting local console on http://127.0.0.1:%PORT%/ (server will open the browser)...
    %PYEXE% "%~dp0repolens.py" serve --repo "%REPO%" --port %PORT%
    if errorlevel 1 (
        echo.
        echo [RepoLens] server exited with an error. Read the message above.
        pause
    )
) else (
    echo [RepoLens] port %PORT% already in use - assuming a server is running, opening the page...
    start "" "http://127.0.0.1:%PORT%/"
)

endlocal
