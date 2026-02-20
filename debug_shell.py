import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
project_root = Path(__file__).parent.resolve()
sys.path.append(str(project_root))

# Load env
load_dotenv()

# Import core modules for easy access
from personal_brain.core.database import init_db, get_db_connection, get_all_files, get_file, delete_file_record
from personal_brain.core.indexer import extract_text, generate_embedding
from personal_brain.core.ingestion import ingest_path, process_file, refresh_index_for_file
from personal_brain.core.search import search_files
from personal_brain.core.reranker import rerank_documents
from personal_brain.core.ask import ask_brain
from personal_brain.utils.mineru import MinerUClient
from personal_brain.utils.aliyun_oss import AliyunOSS
from personal_brain.config import STORAGE_PATH, DB_PATH

def help_msg():
    print("\n" + "="*50)
    print(" 🧠 PersonalBrain Debug Shell")
    print("="*50)
    print("Available modules and functions:")
    print("  - Database: init_db, get_all_files, get_file(id), delete_file_record(id)")
    print("  - Ingestion: ingest_path(path), process_file(path), refresh_index_for_file(id)")
    print("  - Search: search_files(query), rerank_documents(query, docs)")
    print("  - AI: ask_brain(query)")
    print("  - Utils: MinerUClient, AliyunOSS")
    print("  - Config: STORAGE_PATH, DB_PATH")
    print("\nExample usage:")
    print("  >>> files = get_all_files()")
    print("  >>> search_files('python tutorial')")
    print("  >>> mineru = MinerUClient()")
    print("="*50 + "\n")

if __name__ == "__main__":
    help_msg()
    
    # Try to use IPython if available, otherwise code.interact
    try:
        from IPython import start_ipython
        start_ipython(argv=[], user_ns=locals())
    except ImportError:
        import code
        code.interact(local=locals())
