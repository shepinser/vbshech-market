@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
 echo First run the main launcher to install dependencies.
 pause
 exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0manage.py"
pause
