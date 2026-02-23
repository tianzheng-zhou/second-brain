import subprocess
import sys
import time
import os
import signal

def main():
    print("🚀 Starting PersonalBrain Services...")
    
    # Get the current Python interpreter
    python_exe = sys.executable
    
    # Start Chainlit App (User Interface)
    print("\n[1/2] Launching User Interface (Chainlit)...")
    # We use Popen to run it in the background
    app_process = subprocess.Popen([python_exe, "start_app.py"])
    
    # Start Admin Dashboard (Streamlit)
    print("[2/2] Launching Admin Dashboard (Streamlit)...")
    admin_process = subprocess.Popen([python_exe, "start_admin.py"])
    
    print("\n✅ Services are running in the background!")
    print(f"   - 💬 User Chat:  http://localhost:8000")
    print(f"   - 🛠️ Admin Dash: http://localhost:8502")
    print("\n(Press Ctrl+C in this terminal to stop all services)")
    
    try:
        # Keep the script running to monitor child processes
        while True:
            time.sleep(1)
            
            # Check if any process has exited unexpectedly
            if app_process.poll() is not None:
                print("\n⚠️ User Interface process ended unexpectedly.")
                break
            if admin_process.poll() is not None:
                print("\n⚠️ Admin Dashboard process ended unexpectedly.")
                break
                
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping all services...")
        
        # Terminate processes
        app_process.terminate()
        admin_process.terminate()
        
        # Wait for them to close
        app_process.wait()
        admin_process.wait()
        
        print("👋 Goodbye!")

if __name__ == "__main__":
    main()
