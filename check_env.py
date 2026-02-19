import sys
import requests
import os
from personal_brain.config import OLLAMA_BASE_URL, EMBEDDING_MODEL, VISION_MODEL

def check_ollama():
    print(f"Checking Ollama status at {OLLAMA_BASE_URL}...")
    try:
        # Check if Ollama is running
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags")
        if response.status_code == 200:
            print("✅ Ollama is running.")
            models = [m['name'] for m in response.json()['models']]
            print(f"Found {len(models)} models.")
            
            # Check for embedding model
            if any(EMBEDDING_MODEL in m for m in models):
                print(f"✅ Embedding model '{EMBEDDING_MODEL}' found.")
            else:
                print(f"❌ Embedding model '{EMBEDDING_MODEL}' NOT found.")
                print(f"   Please run: ollama pull {EMBEDDING_MODEL}")
                
            # Check for vision model
            if any(VISION_MODEL in m for m in models):
                print(f"✅ Vision model '{VISION_MODEL}' found.")
            else:
                print(f"❌ Vision model '{VISION_MODEL}' NOT found.")
                print(f"   Please run: ollama pull {VISION_MODEL}")
                
        else:
            print(f"❌ Ollama responded with status code {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to Ollama. Is it installed and running?")
        print("   Download from: https://ollama.com/")
        print("   Default port is 11434.")
    except Exception as e:
        print(f"❌ Error checking Ollama: {e}")

if __name__ == "__main__":
    check_ollama()
