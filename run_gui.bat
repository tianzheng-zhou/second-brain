@echo off
cd /d "%~dp0"
echo Starting PersonalBrain GUI...

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m streamlit run streamlit_app.py
) else (
    echo Virtual environment not found in .venv.
    echo Please ensure you have set up the environment correctly by running:
    echo python -m venv .venv
    echo .venv\Scripts\pip install -r requirements.txt
)

pause
