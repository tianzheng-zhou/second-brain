@echo off
cd /d "%~dp0"
echo Starting PersonalBrain MCP Server...

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" mcp_server.py
) else (
    echo Virtual environment not found in .venv.
    echo Please ensure you have set up the environment correctly.
)
pause
