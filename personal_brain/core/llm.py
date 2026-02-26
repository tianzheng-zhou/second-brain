from openai import OpenAI
from personal_brain.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from personal_brain.core.config_manager import config_manager

# Models that support thinking/reasoning mode
QWEN3_THINKING_MODELS = ["qwen3.5-flash", "qwen3.5-flash-thinking", "qwen3-long", "qwen3-long-thinking"]

def get_client():
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY is not set.")
    return OpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)


def is_thinking_model(model: str) -> bool:
    """Check if a model supports thinking/reasoning mode."""
    return any(thinking_model in model for thinking_model in QWEN3_THINKING_MODELS)


def call_llm(messages, model=None, tools=None, tool_choice=None, stream=False, enable_thinking=False):
    """
    Call LLM with optional thinking mode control.

    Args:
        messages: Chat messages history
        model: Model name (defaults to config's chat_model)
        tools: Optional tools/functions
        tool_choice: Tool choice strategy
        stream: Whether to stream response
        enable_thinking: Enable thinking/reasoning mode (for qwen3.5-flash etc.)

    Returns:
        Completion response
    """
    client = get_client()
    model = model or config_manager.get("chat_model")

    extra_body = {}
    # Only add enable_thinking if the model supports it
    if is_thinking_model(model) and not enable_thinking:
        extra_body["enable_thinking"] = False

    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0.7,
        stream=stream,
        extra_body=extra_body if extra_body else None
    )
