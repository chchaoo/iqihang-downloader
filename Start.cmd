@echo off
setlocal
pushd "%~dp0" || exit /b 1
where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -B app.py
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        echo Python 3.10+ with Tkinter is required. Add Python to PATH and try again.
        pause
        exit /b 1
    )
    py.exe -3 -B app.py
)
if errorlevel 1 pause
popd
