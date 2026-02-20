import os
import sys
import subprocess
from pathlib import Path

def main():
    """
    Launch the Streamlit application within the virtual environment.
    """
    project_root = Path(__file__).parent.resolve()
    venv_path = project_root / ".venv"
    
    # Check if running in virtual environment
    # If sys.prefix == sys.base_prefix, we are NOT in a virtual environment (usually)
    is_in_venv = sys.prefix != sys.base_prefix
    
    if not is_in_venv and venv_path.exists():
        print(f"Detected virtual environment at {venv_path}")
        
        # Determine python executable in venv
        if sys.platform == "win32":
            python_executable = venv_path / "Scripts" / "python.exe"
        else:
            python_executable = venv_path / "bin" / "python"
            
        if python_executable.exists():
            print(f"Relaunching with {python_executable}...")
            # Relaunch script with venv python
            subprocess.run([str(python_executable), __file__] + sys.argv[1:])
            return

    # If we are here, we are either in venv or venv not found/not used
    # But check if we are in the main execution block or re-exec block
    # Actually, the logic above is:
    # 1. If NOT in venv, find venv python and re-run THIS script with it.
    # 2. If IN venv (or venv not found), run the target app.
    
    # So below is the target app execution logic
    print("Starting Chainlit app...")
    
    # Use chainlit module execution
    # python -m chainlit run chainlit_app.py -w
    target_script = project_root / "chainlit_app.py"
    
    cmd = [sys.executable, "-m", "chainlit", "run", str(target_script), "-w"]
    
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"Error launching app: {e}")

if __name__ == "__main__":
    main()
