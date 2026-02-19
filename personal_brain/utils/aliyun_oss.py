import oss2
from pathlib import Path
from personal_brain.config import (
    ALIYUN_ACCESS_KEY_ID,
    ALIYUN_ACCESS_KEY_SECRET,
    ALIYUN_OSS_ENDPOINT,
    ALIYUN_OSS_BUCKET
)

class AliyunOSS:
    def __init__(self):
        if not all([ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_OSS_ENDPOINT, ALIYUN_OSS_BUCKET]):
            # Return None or handle gracefully if config missing?
            # For now raise error as this class is explicitly called when PDF processing is needed
            raise ValueError("Aliyun OSS configuration is incomplete. Please check your .env file.")
        
        # Ensure endpoint doesn't have http/https prefix if oss2 adds it, 
        # but usually oss2 handles it. Better to keep it clean.
        endpoint = ALIYUN_OSS_ENDPOINT
        if not endpoint.startswith("http"):
             endpoint = f"https://{endpoint}"

        self.auth = oss2.Auth(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET)
        self.bucket = oss2.Bucket(self.auth, endpoint, ALIYUN_OSS_BUCKET)

    def upload_file(self, file_path: Path, object_name: str = None) -> str:
        """Upload a file to OSS and return the object name (key)."""
        if object_name is None:
            # Use a temp folder to avoid clutter
            object_name = f"temp_pdf/{file_path.name}"
        
        print(f"Uploading {file_path} to OSS as {object_name}...")
        self.bucket.put_object_from_file(object_name, str(file_path))
        return object_name

    def sign_url(self, object_name: str, expiration: int = 3600) -> str:
        """Generate a signed URL for reading the file."""
        return self.bucket.sign_url('GET', object_name, expiration)

    def delete_file(self, object_name: str):
        """Delete the file from OSS."""
        print(f"Deleting {object_name} from OSS...")
        self.bucket.delete_object(object_name)
