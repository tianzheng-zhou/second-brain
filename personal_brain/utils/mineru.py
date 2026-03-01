import requests
import time
import zipfile
import io
from pathlib import Path
from personal_brain.config import MINERU_API_TOKEN, MINERU_BASE_URL, MINERU_USE_SYSTEM_PROXY

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

        # Configure proxy settings
        if MINERU_USE_SYSTEM_PROXY:
            self.proxies = None  # Use system/env proxies
        else:
            self.proxies = {"http": None, "https": None}  # Disable proxies

    def submit_task(self, file_url: str, is_ocr: bool = False, model_version: str = "vlm") -> str:
        """Submit a PDF extraction task."""
        url = f"{self.base_url}/extract/task"
        data = {
            "url": file_url,
            "is_ocr": is_ocr,
            "model_version": model_version,
            "enable_table": True,
            "enable_formula": True
        }
        
        print(f"Submitting MinerU task for URL: {file_url[:50]}... (model: {model_version})")
        resp = requests.post(url, headers=self.headers, json=data, proxies=self.proxies)
        
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
        resp = requests.get(url, headers=self.headers, proxies=self.proxies)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(self, task_id: str, timeout: int = 3600) -> str:
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

    def download_and_extract_markdown(self, zip_url: str, save_dir: Path = None) -> Path:
        """
        Download the result ZIP and extract it to a directory.
        Returns the path to the main markdown file.
        """
        print(f"Downloading result to {save_dir}...")
        
        # Retry mechanism for download
        max_retries = 3
        retry_delay = 2
        
        resp = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(zip_url, timeout=300, proxies=self.proxies) # Add timeout
                resp.raise_for_status()
                break # Success
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, requests.exceptions.Timeout) as e:
                if attempt < max_retries - 1:
                    print(f"Download failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2 # Exponential backoff
                else:
                    raise Exception(f"Failed to download result after {max_retries} attempts: {e}")
            except Exception as e:
                # Other errors (e.g. 404, 403) - don't retry
                raise e
        
        if not resp:
             raise Exception("Download failed (empty response)")

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            # Find the markdown file
            md_files = [f for f in z.namelist() if f.endswith('.md') and not f.startswith('__MACOSX')]
            
            if not md_files:
                # List all files for debugging
                print(f"Files in zip: {z.namelist()}")
                raise Exception("No markdown file found in MinerU result")
            
            # Prefer the one that matches the original filename if possible, or just the first one
            target_file = md_files[0]
            
            # If save_dir is provided, extract everything there
            if save_dir:
                save_dir.mkdir(parents=True, exist_ok=True)
                z.extractall(save_dir)
                return save_dir / target_file
            else:
                # Fallback to in-memory read (legacy behavior, but discouraged now)
                # This path is mainly for backward compatibility if needed, 
                # but better to force extraction for images.
                # Let's just raise an error or return content if no save_dir provided?
                # For now, let's keep the old behavior if save_dir is None, 
                # but return string content (which breaks type hint, but python is dynamic).
                # Wait, let's just return the content as a Path-like object (StringIO)? No.
                # Let's just assume save_dir is always provided now.
                print("Warning: No save_dir provided, returning content string (Legacy). Images will be lost.")
                with z.open(target_file) as f:
                    return f.read().decode('utf-8')
