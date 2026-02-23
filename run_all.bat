@echo off
echo Starting PersonalBrain Services...
echo.
echo Launching User Interface and Admin Dashboard...
call .venv\Scripts\activate
python start_all.py
pause