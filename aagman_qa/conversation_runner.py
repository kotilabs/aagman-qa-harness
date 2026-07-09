"""Generic chat-loop runner that handles back-and-forth agent conversations."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .browser import Browser
from .checks import TestResult, capture_failure_screenshot
from .conversation import extract_messages, latest_assistant_message, scroll_chat_to_bottom
from .llm import decide_reply


SubmitPrompt = Callable[[str], None]


def _wait_for_stable_assistant_message(
    browser: Browser,
    known_user_texts: list[str],
    timeout: float,
    stable_for_seconds: float = 3.0,
    poll_interval: float = 1.0,
) -> str | None:
    """Wait until the latest assistant message stops changing.

    This avoids reacting to streaming tokens before the response is complete.
    """
    deadline = time.time() + timeout
    last_text: str | None = None
    stable_since: float | None = None

    while time.time() < deadline:
        scroll_chat_to_bottom(browser)
        messages = extract_messages(browser)
        current = latest_assistant_message(messages, known_user_texts)

        if current != last_text:
            last_text = current
            stable_since = time.time()
        elif current and stable_since and (time.time() - stable_since) >= stable_for_seconds:
            return current

        time.sleep(poll_interval)

    return last_text


def _contains_any(text: str, markers: list[str]) -> str | None:
    lowered = text.lower()
    for marker in markers:
        if marker.lower() in lowered:
            return marker
    return None


def run_conversation_test(
    browser: Browser,
    test: dict,
    artifact_dir: Path,
    submit_prompt: SubmitPrompt,
    expected_markers: list[str] | None = None,
    error_markers: list[str] | None = None,
) -> TestResult:
    """Run a chat-based test that may require several intelligent replies.

    Parameters
    ----------
    browser:
        Active Browser instance already on the right page.
    test:
        Manifest test entry. Must contain ``id`` and ``prompt``.
    artifact_dir:
        Directory where failure screenshots are saved.
    submit_prompt:
        Callable that submits a user message string to the chat.
    expected_markers:
        Substrings that indicate a successful final result.
    error_markers:
        Substrings that indicate failure.
    """
    test_id = test["id"]
    original_prompt = test["prompt"]
    expected = expected_markers or test.get("expected_contains", [])
    errors = error_markers or test.get("error_markers", [])
    timeout = float(test.get("timeout", 180))

    result = TestResult(id=test_id, status="PASS", duration_sec=0.0, prompt=original_prompt)
    start = time.time()
    sent_user_texts: list[str] = [original_prompt]

    def remaining() -> float:
        return timeout - (time.time() - start)

    def fail(message: str) -> None:
        result.status = "FAIL"
        result.message = message
        result.duration_sec = round(time.time() - start, 2)
        ss_path = artifact_dir / f"{test_id}_fail.png"
        try:
            capture_failure_screenshot(browser, ss_path)
            result.add_screenshot(ss_path)
            result.add_log(f"Screenshot captured: {ss_path}")
        except Exception as ss_exc:
            result.add_log(f"Screenshot failed: {ss_exc}")

    try:
        submit_prompt(original_prompt)
        result.add_log(f"Submitted prompt: {original_prompt[:120]}...")

        prev_assistant_text: str | None = None

        while remaining() > 0:
            latest = _wait_for_stable_assistant_message(
                browser,
                sent_user_texts,
                timeout=remaining(),
                stable_for_seconds=3.0,
            )

            if not latest:
                fail("Timed out waiting for an assistant response.")
                return result

            if latest == prev_assistant_text:
                # Nothing new appeared after our last reply; wait a bit more.
                time.sleep(2)
                continue

            prev_assistant_text = latest
            result.add_log(f"Assistant: {latest[:160]}...")

            # Fast path: final result or hard error.
            matched_error = _contains_any(latest, errors)
            if matched_error:
                raise RuntimeError(f"Error marker detected: {matched_error}")

            matched_expected = _contains_any(latest, expected)
            if matched_expected:
                result.add_log(f"Success marker found: {matched_expected}")
                result.duration_sec = round(time.time() - start, 2)
                return result

            # Fast path: known Aagman workflow confirmations.
            lower_latest = latest.lower()
            if "run risk checks" in lower_latest:
                reply = "run risk checks"
            elif "run backtest" in lower_latest:
                reply = "run backtest"
            else:
                decision = decide_reply(latest, original_prompt, expected, errors)
                result.add_log(f"LLM decision: {decision.action} — {decision.reason}")

                if decision.action == "pass":
                    result.add_log("LLM judged the assistant response as final.")
                    result.duration_sec = round(time.time() - start, 2)
                    return result
                if decision.action == "fail":
                    raise RuntimeError(f"LLM judged failure: {decision.reason}")
                if decision.action == "wait":
                    time.sleep(3)
                    continue
                # action == 'reply'
                reply = decision.message

            if not reply:
                raise RuntimeError("No reply produced for assistant question.")

            submit_prompt(reply)
            sent_user_texts.append(reply)
            result.add_log(f"Replied: {reply[:120]}...")

        fail("Timed out before the conversation reached a final result.")
    except Exception as exc:
        fail(str(exc))

    return result
