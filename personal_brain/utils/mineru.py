import requests
import time
import zipfile
import io
from pathlib import Path
from personal_brain.config import MINERU_API_TOKEN, MINERU_BASE_URL

class MinerUClient:
    def __init__(self):
        if not MINERU_API_TOKEN:
            raise ValueError("MinerU API Token is missing. Please check your .env file.")
        
        # Clean base URL
        self.base_url = MINERU_BASE_URL.rstrip('/')
        
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MINERU_API_TOKEN}"
        }

    def submit_task(self, file_url: str, is_ocr: bool = False) -> str:
        """Submit a PDF extraction task."""
        url = f"{self.base_url}/extract/task"
        data = {
            "url": file_url,
            "is_ocr": is_ocr,
            "model_version": "vlm",  # Use VLM as per user preference/example
            "enable_table": True,
            "enable_formula": True
        }
        
        print(f"Submitting MinerU task for URL: {file_url[:50]}...")
        resp = requests.post(url, headers=self.headers, json=data)
        
        try:
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            print(f"MinerU API Error: {resp.text}")
            raise e

        if result["code"] != 0:
            raise Exception(f"MinerU submit failed: {result['msg']}")
        
        task_id = result["data"]["task_id"]
        print(f"Task submitted successfully. Task ID: {task_id}")
        return task_id

    def get_task_status(self, task_id: str):
        """Check task status."""
        url = f"{self.base_url}/extract/task/{task_id}"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(self, task_id: str, timeout: int = 600) -> str:
        """Poll for task completion and return the download URL (zip)."""
        print(f"Waiting for task {task_id} to complete...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            res = self.get_task_status(task_id)
            if res["code"] != 0:
                raise Exception(f"MinerU check failed: {res['msg']}")
            
            data = res["data"]
            state = data["state"]
            
            if state == "done":
                print("Task completed!")
                return data["full_zip_url"]
            elif state == "failed":
                raise Exception(f"MinerU task failed: {data.get('err_msg')}")
            elif state in ["running", "pending", "converting"]:
                # Print progress if available
                if "extract_progress" in data:
                    progress = data["extract_progress"]
                    extracted = progress.get("extracted_pages", 0)
                    total = progress.get("total_pages", "?")
                    print(f"Processing... {extracted}/{total} pages")
            
            time.sleep(5)
            
        raise TimeoutError("MinerU task timed out")

    def download_and_extract_markdown(self, zip_url: str) -> str:
        """Download the result ZIP and extract the markdown content."""
        print("Downloading result...")
        resp = requests.get(zip_url)
        resp.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            # Find the markdown file
            # Usually named {filename}.md or inside a folder
            # Let's look for any .md file
            md_files = [f for f in z.namelist() if f.endswith('.md') and not f.startswith('__MACOSX')]
            
            if not md_files:
                # List all files for debugging
                print(f"Files in zip: {z.namelist()}")
                raise Exception("No markdown file found in MinerU result")
            
            # Prefer the one that matches the original filename if possible, or just the first one
            # Usually MinerU puts it in a folder structure. 
            # e.g. "demo/demo.md"
            target_file = md_files[0]
            print(f"Extracting content from {target_file}")
            
            with z.open(target_file) as f:
                return f.read().decode('utf-8')
