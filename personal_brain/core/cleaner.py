from datetime import datetime, timedelta
from personal_brain.core.models import File, FileType

def calculate_trash_score(file: File) -> float:
    """
    Calculate trash score based on file properties.
    0.0 = Absolute Trash
    1.0 = Very Important
    """
    score = 1.0
    
    # Rule 1: No text content
    # If text is None, it might not be processed yet, but here we assume it is.
    text_len = len(file.ocr_text) if file.ocr_text else 0
    if text_len < 10:
        score -= 0.5
        
    # Rule 2: Small image
    if file.type == FileType.IMAGE and file.size_bytes < 50 * 1024: # 50KB
        score -= 0.3
        
    # Rule 3: Screenshot
    if "screenshot" in file.filename.lower():
        score -= 0.2
        
    # Rule 4: Time decay (90 days)
    now = datetime.now()
    if file.last_accessed and (now - file.last_accessed).days > 90:
        score -= 0.2
        
    # Rule 5: Recent protection (7 days)
    if file.created_at and (now - file.created_at).days < 7:
        score += 0.1
        
    return max(0.0, min(1.0, score))
