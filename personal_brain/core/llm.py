from openai import OpenAI
from personal_brain.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from personal_brain.core.config_manager import config_manager

def get_client():
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY is not set.")
    return OpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)

def call_llm(messages, model=None, tools=None, tool_choice=None, stream=False):
    client = get_client()
    model = model or config_manager.get("chat_model")
    
    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0.7,
        stream=stream
    )
