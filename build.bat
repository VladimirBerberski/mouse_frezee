@echo off
REM ---------------------------------------------------------------
REM Build FreezeMouse.exe from freezemouse.py using PyInstaller.
REM Keep this file, freezemouse.py and freezemouse.ico in the same
REM folder, then double-click this file (or run it in a terminal).
REM ---------------------------------------------------------------

cd /d "%~dp0"

echo Installing / updating PyInstaller...
python -m pip install --upgrade pyinstaller || goto :error

echo.
echo Building FreezeMouse.exe ...
python -m PyInstaller --onefile --noconsole --name FreezeMouse ^
    --icon freezemouse.ico --add-data "freezemouse.ico;." freezemouse.py || goto :error

echo.
echo ============================================================
echo  Done!  Your executable is here:
echo     dist\FreezeMouse.exe
echo ============================================================
pause
exit /b 0

:error
echo.
echo Build failed. Make sure Python is installed and on your PATH
echo (try: python --version).
pause
exit /b 1
