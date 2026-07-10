"""Vision-based verification of browser screenshots.

Uses an OpenAI-compatible vision API to evaluate whether a screenshot shows the
expected content (e.g., a price chart for a specific symbol).
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import config


class VisionError(Exception):
    pass


def _encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def evaluate_screenshot(
    image_path: Path,
    question: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    """Ask a vision model a yes/no question about a screenshot.

    Returns a dict like:
        {"answer": "yes", "reason": "The screenshot shows a candlestick chart for INFY."}

    Raises VisionError if the API call fails or the response cannot be parsed.
    """
    api_key = api_key or config.vision_api_key()
    base_url = (base_url or config.vision_base_url()).rstrip("/")
    model = model or config.vision_model()

    if not api_key:
        raise VisionError("No vision API key configured. Set VISION_API_KEY or OPENAI_API_KEY.")

    image_b64 = _encode_image(image_path)
    mime = _mime_type(image_path)
    data_url = f"data:{mime};base64,{image_b64}"

    system_prompt = (
        "You are a test verifier. Look at the screenshot and answer the user's "
        "question with a single JSON object containing exactly two keys: "
        "'answer' (either 'yes' or 'no') and 'reason' (one short sentence). "
        "Be strict but fair."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": question},
                ],
            },
        ],
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "aagman-qa-harness/0.1.0",
        },
        method="POST",
    )

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
            result = json.loads(response.read().decode("utf-8"))
            raw = result["choices"][0]["message"]["content"]
            raw = raw.strip() if raw else ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise VisionError(f"Vision API HTTP {exc.code}: {body[:300]}") from exc
    except Exception as exc:
        raise VisionError(f"Vision API call failed: {exc}") from exc

    # Some vision models (including kimi-for-coding occasionally) return an empty
    # content block. Retry once quickly before giving up.
    if not raw:
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
                result = json.loads(response.read().decode("utf-8"))
                raw = result["choices"][0]["message"]["content"]
                raw = raw.strip() if raw else ""
        except Exception:
            pass
        if not raw:
            raise VisionError("Vision model returned an empty response")

    # Clean up markdown fences if present.
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()

    parsed = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: look for a bare "yes" or "no" in the raw text.
        lowered = cleaned.lower()
        if "yes" in lowered and "no" not in lowered:
            parsed = {"answer": "yes", "reason": "Model responded affirmatively."}
        elif "no" in lowered and "yes" not in lowered:
            parsed = {"answer": "no", "reason": "Model responded negatively."}

    if not parsed:
        raise VisionError(f"Could not parse vision response as JSON: {raw}")

    answer = str(parsed.get("answer", "")).strip().lower()
    if answer not in ("yes", "no"):
        raise VisionError(f"Vision answer was not yes/no: {raw}")

    return {
        "answer": answer,
        "reason": str(parsed.get("reason", "")).strip() or "No reason provided.",
    }


def verify_chart(
    image_path: Path,
    symbol: Optional[str] = None,
    description: Optional[str] = None,
    **kwargs,
) -> dict:
    """Convenience wrapper: does the screenshot show a chart for the given symbol?"""
    parts = ["Does this screenshot show a price chart"]
    if symbol:
        parts.append(f"for {symbol}")
    if description:
        parts.append(f"({description})")
    parts.append("? Reply yes if there is a visible chart (candlestick, line, bar, or OHLC table). Reply no if there is only text or no relevant chart.")
    question = " ".join(parts)
    return evaluate_screenshot(image_path, question, **kwargs)
