"""LLM decision helper for handling unexpected agent replies.

Uses the DeepSeek API (OpenAI-compatible) by default. The model only needs to
classify intent and produce a short reply, so a fast/cheap model is sufficient.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    action: str  # 'reply' | 'wait' | 'pass' | 'fail'
    message: str
    reason: str


class LLMConfigError(Exception):
    pass


class LLMResponseError(Exception):
    pass


def _get_client() -> Any:
    try:
        import openai
    except ImportError as exc:  # pragma: no cover
        raise LLMConfigError("The 'openai' package is required for intelligent replies. Install with: uv add openai") from exc

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise LLMConfigError("DEEPSEEK_API_KEY is not set. Set it in your .env or environment.")

    base_url = os.getenv("AAGMAN_QA_LLM_BASE_URL", "https://api.deepseek.com/v1")
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def _model() -> str:
    return os.getenv("AAGMAN_QA_LLM_MODEL", "deepseek-chat")


def _system_prompt(
    original_prompt: str,
    expected_markers: list[str],
    error_markers: list[str],
) -> str:
    return (
        "You are a test harness helper deciding how to respond to an AI agent "
        "that is running a backtest task for a user.\n\n"
        f"The user originally asked: {original_prompt!r}\n\n"
        "The assistant (Aagman agent) will send a message. Based on that message, "
        "decide the next action.\n\n"
        "Respond with a single JSON object containing exactly these keys:\n"
        "  action: one of 'reply', 'wait', 'pass', 'fail'\n"
        "  message: a short reply string (only used when action='reply')\n"
        "  reason: one-sentence explanation of your decision\n\n"
        "Action rules:\n"
        "- 'pass': the assistant has produced the final backtest result/report.\n"
        "- 'fail': the assistant reported an error, says it cannot proceed, or is stuck.\n"
        "- 'wait': the assistant is still typing/processing and the message is incomplete.\n"
        "- 'reply': the assistant asked a question, needs confirmation, or is waiting for input.\n\n"
        "Reply rules (only when action='reply'):\n"
        "- If the assistant asks for confirmation, answer with 'yes' or 'proceed'.\n"
        "- If the assistant asks a clarifying question, answer using ONLY the original user request.\n"
        "  Do not invent new symbols, dates, strategy parameters, or numbers.\n"
        "- If the assistant asks to run a check (e.g. 'run risk checks'), reply exactly that phrase.\n"
        "- Keep the reply to one short sentence.\n\n"
        f"Expected final result markers: {expected_markers}\n"
        f"Error markers: {error_markers}\n"
    )


def _parse_decision(raw: str) -> Decision:
    # Some models wrap JSON in markdown fences; strip them.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"Could not parse LLM response as JSON: {raw}") from exc

    action = str(data.get("action", "")).lower().strip()
    message = str(data.get("message", "")).strip()
    reason = str(data.get("reason", "")).strip()

    if action not in {"reply", "wait", "pass", "fail"}:
        # If the model gave a sensible message but no valid action, assume reply.
        if message:
            action = "reply"
        else:
            raise LLMResponseError(f"Invalid action from LLM: {action}. Raw: {raw}")

    return Decision(action=action, message=message, reason=reason)


def decide_reply(
    latest_assistant_msg: str,
    original_prompt: str,
    expected_markers: list[str],
    error_markers: list[str],
) -> Decision:
    """Ask the LLM what to do about the latest assistant message."""
    client = _get_client()
    user_content = f"Assistant just said:\n{latest_assistant_msg}\n\nWhat should the test harness do next?"

    response = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _system_prompt(original_prompt, expected_markers, error_markers)},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        max_tokens=256,
    )

    raw = response.choices[0].message.content or ""
    return _parse_decision(raw)
