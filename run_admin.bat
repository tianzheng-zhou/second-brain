@echo off
echo Starting PersonalBrain Admin Console...
call .venv\Scripts\activate
streamlit run admin_dashboard.py
pause
