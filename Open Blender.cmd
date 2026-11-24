@echo off
setlocal
where py >nul 2>&1
if errorlevel 1 (
    echo This launcher needs the Python 3 launcher for Windows.
    pause
    exit /b 1
)
if not "%~1"=="" (
    py -3 "%~dp0tools\blender.py" open "%~f1"
) else if exist "%~dp0build\demo\Dress form.blend" (
    py -3 "%~dp0tools\blender.py" open "%~dp0build\demo\Dress form.blend"
) else (
    py -3 "%~dp0tools\blender.py" open
)
if errorlevel 1 (
    pause
    exit /b 1
)
