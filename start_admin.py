import sys
import os
from pathlib import Path

def main():
    """
    Launch the Admin Dashboard (Streamlit) for debugging.
    This script allows you to run the Streamlit app directly from your IDE 
    and attach a debugger to the process.
    """
    project_root = Path(__file__).parent.resolve()
    venv_path = project_root / ".venv"
    
    # 1. Virtual Environment Check & Relaunch
    # If we are not in the venv, try to relaunch with the venv python
    # This ensures dependencies are found even if you just run 'python start_admin.py'
    is_in_venv = sys.prefix != sys.base_prefix
    if not is_in_venv and venv_path.exists():
        print(f"Detected virtual environment at {venv_path}")
        if sys.platform == "win32":
            python_executable = venv_path / "Scripts" / "python.exe"
        else:
            python_executable = venv_path / "bin" / "python"
            
        if python_executable.exists():
            print(f"Relaunching with {python_executable}...")
            import subprocess
            # Relaunch this script using the venv python
            subprocess.run([str(python_executable), __file__] + sys.argv[1:])
            return

    # 2. Launch Streamlit In-Process
    # We use streamlit.web.cli.main() to run the server in the current process.
    # This allows IDE debuggers (VS Code, PyCharm) to hit breakpoints in your code.
    print("Starting Admin Dashboard (Streamlit)...")
    
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("Error: Streamlit is not installed in the current environment.")
        print("Please install it with: pip install streamlit")
        return

    target_script = project_root / "admin_dashboard.py"
    
    if not target_script.exists():
        print(f"Error: Could not find {target_script}")
        return

    # Mock command line arguments for Streamlit
    # equivalent to: streamlit run admin_dashboard.py --server.port=8502
    sys.argv = [
        "streamlit",
        "run",
        str(target_script),
        "--server.port=8502",
        "--server.headless=false" # Set to true to prevent opening browser automatically
    ]
    
    print(f"Running: {' '.join(sys.argv)}")
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()
