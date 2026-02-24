import os
import time
import json
import requests
from pathlib import Path
from personal_brain.config import DASHSCOPE_API_KEY
from personal_brain.utils.aliyun_oss import AliyunOSS

class ASRClient:
    API_URL_SUBMIT = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
    API_URL_QUERY_BASE = "https://dashscope.aliyuncs.com/api/v1/tasks/"
    MODEL_NAME = "qwen3-asr-flash-filetrans"

    def __init__(self):
        if not DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY is not set.")
        self.api_key = DASHSCOPE_API_KEY
        self.oss = AliyunOSS()

    def transcribe(self, file_path: Path) -> str:
        """
        Transcribe audio file using Qwen3-ASR-Flash.
        Uploads file to OSS, submits task, polls for result, cleans up OSS.
        """
        object_name = None
        try:
            # 1. Upload to OSS
            print(f"Uploading audio {file_path.name} to OSS...")
            # Use a specific folder for audio to keep organized
            object_name = f"temp_audio/{file_path.name}"
            self.oss.upload_file(file_path, object_name)

            # 2. Get Signed URL (valid for 1 hour is enough)
            file_url = self.oss.sign_url(object_name, expiration=3600)
            
            # 3. Submit Task
            print("Submitting ASR task...")
            task_id = self._submit_task(file_url)
            if not task_id:
                return ""

            # 4. Poll for Result
            print(f"Waiting for ASR task {task_id}...")
            result = self._poll_task(task_id)
            
            # 5. Extract Text
            if result:
                # The result structure from example:
                # {
                #   "output": {
                #     "task_id": "...",
                #     "task_status": "SUCCEEDED",
                #     "results": [
                #       {
                #         "text": "...",
                #         "sentences": [...]
                #       }
                #     ]
                #   }
                # }
                # The user example didn't show exact success output structure, 
                # but typically DashScope ASR returns 'results' list with 'text'.
                # Let's handle generic 'results' or 'text' field if present.
                
                output = result.get("output", {})
                results = output.get("results", [])
                
                full_text = ""
                if results:
                    for res in results:
                        if "text" in res:
                            full_text += res["text"]
                        elif "sentences" in res:
                            for sent in res["sentences"]:
                                full_text += sent.get("text", "")
                
                # If no structured results, check if there is a direct text field (unlikely for this model but safe)
                if not full_text and "text" in output:
                    full_text = output["text"]
                    
                return full_text
            
            return ""

        except Exception as e:
            print(f"ASR Transcription failed: {e}")
            return ""
            
        finally:
            # 6. Cleanup OSS
            if object_name:
                try:
                    self.oss.delete_file(object_name)
                except Exception as e:
                    print(f"Warning: Failed to delete temp audio file from OSS: {e}")

    def _submit_task(self, file_url: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable"
        }
        
        payload = {
            "model": self.MODEL_NAME,
            "input": {
                "file_url": file_url
            },
            "parameters": {
                "channel_id": [0],
                "enable_itn": False
            }
        }
        
        try:
            resp = requests.post(self.API_URL_SUBMIT, headers=headers, json=payload)
            if resp.status_code != 200:
                print(f"ASR Submit Failed: {resp.status_code} - {resp.text}")
                return None
                
            data = resp.json()
            output = data.get("output")
            if output and "task_id" in output:
                return output["task_id"]
            
            print(f"ASR Submit returned unexpected response: {data}")
            return None
            
        except Exception as e:
            print(f"ASR Submit Exception: {e}")
            return None

    def _poll_task(self, task_id: str) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        query_url = self.API_URL_QUERY_BASE + task_id
        
        while True:
            try:
                resp = requests.get(query_url, headers=headers)
                if resp.status_code != 200:
                    print(f"ASR Poll Failed: {resp.status_code} - {resp.text}")
                    return None
                
                data = resp.json()
                output = data.get("output")
                
                if output and "task_status" in output:
                    status = output["task_status"].upper()
                    if status == "SUCCEEDED":
                        return data
                    elif status in ("FAILED", "CANCELED", "UNKNOWN"):
                        print(f"ASR Task {status}: {data}")
                        return None
                    else:
                        # RUNNING, PENDING, etc.
                        time.sleep(2)
                        continue
                else:
                    print(f"ASR Poll returned unexpected data: {data}")
                    return None
                    
            except Exception as e:
                print(f"ASR Poll Exception: {e}")
                return None
