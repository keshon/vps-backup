@echo off
cd /d "%~dp0"
python "%~dp0recover.py" %*
echo.
pause
