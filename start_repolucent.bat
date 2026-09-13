@echo off
rem RepoLucent double-click launcher for Windows.
rem Usage:
rem   start_repolucent.bat                  -> repo taken from repolucent.ini, or pass a path as argument 1
rem   start_repolucent.bat D:\path\to\repo  -> analyze the given repo root
rem Behavior:
rem   - port 8788 free   -> start server; server auto-opens the browser page after about 0.8s
rem   - port 8788 in use -> assume a server is already running, just open the page, do not restart
rem NOTE: keep this file ASCII-only to avoid codepage issues.

setlocal
cd /d "%~dp0"

rem NOTE: PYEXE must stay UNQUOTED on the command lines below because its value can be
rem       the two tokens 'py -3'. Quoting it would make cmd look for a program literally
rem       named py -3 and fail. Real bug found 2026-09-12.
set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
    python --version >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo [RepoLucent] Python 3.10+ not found in PATH. Please install Python first.
    pause
    exit /b 1
)

rem --- resolve repo: argument 1 overrides repolucent.ini ---
set "REPO="
if not "%~1"=="" set "REPO=%~1"
if not defined REPO (
    if exist repolucent.ini (
        for /f "usebackq eol=# tokens=1,* delims==" %%a in (`findstr /i "^repo=" repolucent.ini`) do set "REPO=%%b"
    )
)
if not defined REPO (
    echo [RepoLucent] repo not specified: set repo= in repolucent.ini or pass a path argument.
    pause
    exit /b 1
)

rem --- port check: if 8788 in use, assume a server is running and just open the page ---
set "PORT=8788"
%PYEXE% -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',8788))==0 else 1)" >nul 2>nul
if errorlevel 1 (
    echo [RepoLucent] starting local console on http://127.0.0.1:%PORT%/ - server will open the browser
    %PYEXE% "%~dp0repolucent.py" serve --repo "%REPO%" --port %PORT%
    if errorlevel 1 (
        echo.
        echo [RepoLucent] server exited with an error. Read the message above.
        pause
    )
) else (
    echo [RepoLucent] port %PORT% already in use - assuming a server is running, opening the page
    start "" "http://127.0.0.1:%PORT%/"
)

endlocal
