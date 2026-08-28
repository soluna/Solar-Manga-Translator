@echo off
chcp 65001 >nul
setlocal
:: One-click launcher for the new frontend (frontend-v3 / InkStage).
:: Bootstraps dependencies (backend + frontend-v3), starts the backend API
:: and the new web UI, then opens a dedicated browser window.
call "%~dp0start.bat" v3
if errorlevel 1 (
    echo.
    echo Startup failed - see messages above.
    pause
)
endlocal