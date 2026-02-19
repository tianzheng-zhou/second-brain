@echo off
cd /d "%~dp0"
echo Starting PersonalBrain MCP Server (SSE Mode)...
echo The server will be available at http://0.0.0.0:8000/sse

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" mcp_server.py --transport sse
) else (
    echo Virtual environment not found in .venv.
    echo Please ensure you have set up the environment correctly.
)
pause
