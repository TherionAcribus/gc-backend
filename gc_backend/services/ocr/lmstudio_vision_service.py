"""Vision OCR service using a LMStudio OpenAI-compatible endpoint.

This module sends an image as a data URL in the messages payload.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from .image_utils import to_data_url

logger = logging.getLogger(__name__)


DEFAULT_STRICT_PROMPT = (
    "Transcris précisément le texte visible sur cette image sans interprétation "
    "ni correction orthographique. Respecte les retours à la ligne."
)


@dataclass(frozen=True)
class VisionOcrResult:
    """Result of a vision OCR call."""

    text: str
    provider: str
    model: str


def strip_thinking_blocks(text: str) -> str:
    """Remove model 'thinking' blocks from a response.

    Some local models emit reasoning using markers like [THINK]...[/THINK] or <think>...</think>.
    We strip them so only the final OCR transcription is kept.
    """
    if not text:
        return ""

    cleaned = text
    cleaned = re.sub(r"\[THINK\].*?\[/THINK\]", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\[ANALYSIS\].*?\[/ANALYSIS\]", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


def normalize_lmstudio_base_url(base_url: str) -> str:
    """Normalize a base URL so it points to the OpenAI-compatible /v1 root."""
    raw = (base_url or "").strip()
    if not raw:
        return "http://localhost:1234/v1"

    raw = raw.rstrip("/")
    if raw.endswith("/v1"):
        return raw
    return f"{raw}/v1"


def build_openai_vision_payload(*, model: str, prompt: str, image_bytes: bytes, max_tokens: int = 1024) -> Dict[str, Any]:
    """Build an OpenAI-compatible payload for vision."""
    data_url, _mime = to_data_url(image_bytes)

    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }


def extract_text_from_openai_response(data: Dict[str, Any]) -> str:
    """Extract assistant text from an OpenAI-compatible response."""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Some providers return structured content parts
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
                return "\n".join(parts).strip()
    return ""


def vision_ocr_via_lmstudio(
    *,
    image_bytes: bytes,
    base_url: str,
    model: str,
    prompt: Optional[str] = None,
    timeout_sec: int = 60,
) -> VisionOcrResult:
    """Run OCR using LMStudio vision model.

    Args:
        image_bytes: Raw image bytes.
        base_url: LMStudio base URL (ex: http://localhost:1234).
        model: Model identifier.
        prompt: Strict transcription prompt.
        timeout_sec: HTTP timeout in seconds.

    Returns:
        VisionOcrResult

    Raises:
        RuntimeError: if the endpoint returns an error or invalid payload.
    """
    if not model or not str(model).strip():
        raise RuntimeError("Missing model for LMStudio vision OCR")

    v1 = normalize_lmstudio_base_url(base_url)
    endpoint = f"{v1}/chat/completions"

    payload = build_openai_vision_payload(
        model=str(model).strip(),
        prompt=(prompt or DEFAULT_STRICT_PROMPT),
        image_bytes=image_bytes,
    )

    logger.info("[vision_ocr] Sending request to %s (model=%s)", endpoint, model)

    try:
        res = requests.post(endpoint, json=payload, timeout=timeout_sec)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"LMStudio request failed: {exc}") from exc

    if res.status_code >= 400:
        raise RuntimeError(f"LMStudio HTTP {res.status_code}: {res.text[:500]}")

    try:
        data = res.json()
    except Exception as exc:
        raise RuntimeError(f"LMStudio returned invalid JSON: {exc} ({res.text[:500]})") from exc

    text = extract_text_from_openai_response(data).strip()
    text = strip_thinking_blocks(text)
    if not text:
        raise RuntimeError("LMStudio returned an empty response")

    return VisionOcrResult(text=text, provider="lmstudio", model=str(model).strip())
