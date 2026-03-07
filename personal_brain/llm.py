"""
llm.py — LLM call wrapper using OpenAI SDK pointed at DashScope.
Supports text and multimodal (vision) calls.
Uses tenacity for retries (max 3, exponential backoff).
Disables thinking mode for qwen3/qwq models by default.

Embeddings use the native DashScope SDK:
- MultiModalEmbedding for qwen3-vl-embedding (2560d)
- TextEmbedding for text-embedding-v* models
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import dashscope
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config_manager
from .config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from .utils.logger import get_module_logger
from .utils.metrics import get_metrics

# Initialize DashScope API key
dashscope.api_key = DASHSCOPE_API_KEY

logger = get_module_logger(__name__)
metrics = get_metrics()

_client: OpenAI | None = None

# Models that use MultiModalEmbedding API
_MULTIMODAL_EMBED_MODELS = frozenset({"qwen3-vl-embedding", "qwen-vl-max-embedding"})


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=DASHSCOPE_BASE_URL,
        )
    return _client


def _is_thinking_model(model_name: str) -> bool:
    return any(k in model_name.lower() for k in ("qwen3", "qwq"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def call_llm(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Call LLM with retry. Returns assistant message content."""
    extra_body: dict[str, Any] = {}
    if _is_thinking_model(model):
        extra_body["enable_thinking"] = False
        # DashScope Qwen3: temperature must be > 0 when thinking is disabled
        if temperature == 0:
            temperature = 0.01

    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body or None,
        )
        metrics.increment("api_call_count", "success")
        return resp.choices[0].message.content or ""
    except Exception as e:
        metrics.increment("api_call_count", "retry")
        logger.warning("LLM call failed, retrying", extra={"model": model, "error": str(e)})
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def call_vision(model: str, prompt: str, image_paths: list[Path]) -> str:
    """Call vision model with image(s). Returns assistant content."""
    content: list[dict[str, Any]] = []
    for img_path in image_paths:
        img_b64 = base64.b64encode(img_path.read_bytes()).decode()
        suffix = img_path.suffix.lower().lstrip(".")
        mime = f"image/{suffix}" if suffix in ("png", "jpg", "jpeg", "webp", "gif") else "image/jpeg"
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{img_b64}"},
        })
    content.append({"type": "text", "text": prompt})

    messages = [{"role": "user", "content": content}]
    extra_body: dict[str, Any] = {}
    if _is_thinking_model(model):
        extra_body["enable_thinking"] = False

    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=4096,
            extra_body=extra_body or None,
        )
        metrics.increment("api_call_count", "success")
        return resp.choices[0].message.content or ""
    except Exception as e:
        metrics.increment("api_call_count", "retry")
        logger.warning("Vision call failed, retrying", extra={"model": model, "error": str(e)})
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def generate_embedding(text: str) -> list[float]:
    """Generate embedding for a single text string."""
    model = config_manager.get("embedding_model")
    try:
        emb = _call_embedding_api(model, [text])
        metrics.increment("api_call_count", "success")
        return emb[0]
    except Exception as e:
        metrics.increment("api_call_count", "retry")
        logger.warning("Embedding call failed, retrying", extra={"model": model, "error": str(e)})
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    model = config_manager.get("embedding_model")
    try:
        embs = _call_embedding_api(model, texts)
        metrics.increment("api_call_count", "success")
        return embs
    except Exception as e:
        metrics.increment("api_call_count", "retry")
        logger.warning("Batch embedding failed, retrying", extra={"model": model, "error": str(e)})
        raise


def _call_embedding_api(model: str, texts: list[str]) -> list[list[float]]:
    """Route to the correct DashScope embedding API based on model type."""
    if model in _MULTIMODAL_EMBED_MODELS:
        return _multimodal_embed(model, texts)
    return _text_embed(model, texts)


def _multimodal_embed(model: str, texts: list[str]) -> list[list[float]]:
    """Use DashScope MultiModalEmbedding for qwen3-vl-embedding etc."""
    from dashscope import MultiModalEmbedding
    results = []
    # MultiModalEmbedding processes one input at a time in batch format
    input_data = [{"text": t} for t in texts]
    resp = MultiModalEmbedding.call(model=model, input=input_data)
    if resp.status_code != 200:
        raise RuntimeError(f"MultiModalEmbedding failed: {resp.message}")
    for item in resp.output["embeddings"]:
        results.append(item["embedding"])
    return results


def _text_embed(model: str, texts: list[str]) -> list[list[float]]:
    """Use DashScope TextEmbedding for text-embedding-v* models."""
    from dashscope import TextEmbedding
    resp = TextEmbedding.call(model=model, input=texts)
    if resp.status_code != 200:
        raise RuntimeError(f"TextEmbedding failed: {resp.message}")
    return [item["embedding"] for item in resp.output["embeddings"]]
