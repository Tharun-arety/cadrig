@echo off
setlocal
set "FREECAD_BIN=C:\Users\tharu\AppData\Local\Programs\FreeCAD 1.1\bin"
set "CADRIG_INSTALLER=%~dp0freecad_workbench\install.py"

tasklist /FI "IMAGENAME eq freecad.exe" /NH | find /I "freecad.exe" >nul
if not errorlevel 1 (
    echo Close every FreeCAD window, then run this launcher again.
    pause
    exit /b 1
)

"%FREECAD_BIN%\FreeCADCmd.exe" "%CADRIG_INSTALLER%"
if errorlevel 1 (
    echo CADRIG installation failed.
    pause
    exit /b 1
)

start "" "%FREECAD_BIN%\freecad.exe"
endlocal
