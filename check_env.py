import os
import sys
from openai import OpenAI
from personal_brain.config import (
    DASHSCOPE_API_KEY, 
    DASHSCOPE_BASE_URL
)
from personal_brain.core.config_manager import config_manager

def check_dashscope():
    print(f"Checking DashScope configuration...")
    print(f"API Key present: {'Yes' if DASHSCOPE_API_KEY else 'No'}")
    print(f"Base URL: {DASHSCOPE_BASE_URL}")
    
    if not DASHSCOPE_API_KEY:
        print("❌ DASHSCOPE_API_KEY is missing. Please set it in your environment.")
        return

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL
    )

    chat_model = config_manager.get("chat_model")
    embedding_model = config_manager.get("embedding_model")
    vision_model = config_manager.get("vision_model")

    try:
        # Simple chat completion test
        print(f"Testing connection with {chat_model}...")
        completion = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "user", "content": "Hello, are you working?"}
            ]
        )
        print(f"✅ Connection successful!")
        print(f"Response: {completion.choices[0].message.content}")
        
        # Test embedding
        print(f"\nTesting embedding with {embedding_model}...")
        res = client.embeddings.create(
            model=embedding_model,
            input="Test embedding",
            dimensions=1024
        )
        emb = res.data[0].embedding
        print(f"✅ Embedding generated. Dimension: {len(emb)}")

        print(f"\nConfiguration summary:")
        print(f"  - Chat Model: {chat_model}")
        print(f"  - Embedding Model: {embedding_model}")
        print(f"  - Vision Model: {vision_model}")
        
    except Exception as e:
        print(f"❌ Error connecting to DashScope: {e}")

if __name__ == "__main__":
    check_dashscope()
