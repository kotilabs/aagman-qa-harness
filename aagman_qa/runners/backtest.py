import time
from pathlib import Path

from ..browser import Browser
from ..checks import (
    TestResult,
    assert_no_error_texts,
    assert_texts_present,
    capture_failure_screenshot,
)
from ..clarifications import detect_clarification
from ..conversation import extract_messages, latest_assistant_message, scroll_chat_to_bottom
from ..interactions import submit_aagman_prompt


def _has_chat_input(browser: Browser) -> bool:
    """True if a chat input textarea is present."""
    return bool(browser.eval("""
(() => {
  const allInputs = [];
  const walk = (root) => {
    root.querySelectorAll('textarea').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  walk(document);
  return allInputs.some(i => (i.placeholder || '').includes('Brief your agents'));
})()
"""))


def _navigate_to_backtest(browser: Browser, base_url: str) -> None:
    # Go to the backtest workspaces route. Submitting a prompt from here creates
    # a fresh backtest chat.
    browser.open(base_url)
    for _ in range(20):
        if _has_chat_input(browser):
            break
        time.sleep(0.5)


def _body_text(browser: Browser) -> str:
    return str(browser.eval("document.body.innerText"))


def _has_report_card(browser: Browser, expected: list[str]) -> bool:
    body = _body_text(browser)
    return all(marker in body for marker in expected)


def _aagman_is_thinking(text: str) -> bool:
    return any(phrase in text for phrase in [
        "Thinking",
        "Designing your strategy",
        "preparing",
        "Aagman is",
        "compiling",
        "Please wait",
    ])


def run(
    browser: Browser,
    base_url: str,
    test: dict,
    artifact_dir: Path,
    answer_provider=None,
) -> TestResult:
    test_id = test["id"]
    prompt = test["prompt"]
    expected = test.get("expected_contains", ["TOTAL PnL", "MAX DRAWDOWN", "WIN RATE"])
    error_markers = test.get("error_markers", [
        "Cannot run backtest",
        "Backtest failed",
        "Unable to run backtest",
        "No data available",
    ])
    timeout = test.get("timeout", 240)

    result = TestResult(id=test_id, status="PASS", duration_sec=0.0, prompt=prompt)
    start = time.time()

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

    def block(question: str) -> None:
        result.status = "BLOCKED"
        result.message = f"Clarification not answered: {question[:300]}"
        result.duration_sec = round(time.time() - start, 2)
        ss_path = artifact_dir / f"{test_id}_blocked.png"
        try:
            capture_failure_screenshot(browser, ss_path)
            result.add_screenshot(ss_path)
            result.add_log(f"Blocked screenshot captured: {ss_path}")
        except Exception as ss_exc:
            result.add_log(f"Blocked screenshot failed: {ss_exc}")

    def _send(command: str) -> None:
        submit_aagman_prompt(browser, command)
        result.add_log(f"Sent: {command}")
        sent_user_texts.append(command)

    def _wait_for_next_assistant_message(
        last_text: str | None,
        wait_timeout: float,
        stable_for: float = 2.0,
    ) -> str | None:
        """Wait until the latest assistant message changes and becomes stable."""
        deadline = time.time() + wait_timeout
        current: str | None = last_text
        stable_since: float | None = None
        while time.time() < deadline and remaining() > 0:
            scroll_chat_to_bottom(browser)
            messages = extract_messages(browser)
            latest = latest_assistant_message(messages, sent_user_texts)
            if latest and latest != current:
                current = latest
                stable_since = time.time()
            elif latest and current == latest and stable_since and (time.time() - stable_since) >= stable_for:
                return current
            time.sleep(1)
        return current if current != last_text else None

    sent_user_texts: list[str] = [prompt]
    last_assistant_text: str | None = None
    last_question: str | None = None
    question_repeat_count = 0

    # Track how many times we have nudged the system; guards against spam loops.
    risk_check_attempts = 0
    backtest_attempts = 0
    max_backtest_attempts = 3
    max_risk_check_attempts = 3

    try:
        _navigate_to_backtest(browser, base_url)
        result.add_log("Navigated to Backtest")

        submit_aagman_prompt(browser, prompt)
        result.add_log(f"Submitted prompt: {prompt[:80]}...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            body = _body_text(browser)

            # 1. Error markers -> fail immediately.
            for marker in error_markers:
                if marker in body:
                    raise RuntimeError(f"Backtest error marker detected: {marker}")

            # 2. Result report card -> pass.
            if _has_report_card(browser, expected):
                assert_no_error_texts(browser, error_markers)
                assert_texts_present(browser, expected)
                result.add_log("Report card markers found")
                result.duration_sec = round(time.time() - start, 2)
                return result

            # 3. Read the latest stable assistant message.
            latest = _wait_for_next_assistant_message(
                last_assistant_text,
                wait_timeout=min(60.0, remaining()),
                stable_for=2.0,
            )

            if latest is None:
                # Nothing new; keep waiting briefly.
                time.sleep(2)
                continue

            last_assistant_text = latest
            result.add_log(f"Assistant: {latest[:160]}...")
            lower = latest.lower()

            # 4. Assistant is still processing; don't send anything.
            if _aagman_is_thinking(latest):
                result.add_log("Assistant is thinking/processing; waiting")
                continue

            # 5. Explicit command requests.
            if "run risk checks" in lower:
                if risk_check_attempts < max_risk_check_attempts:
                    risk_check_attempts += 1
                    _send("run risk checks")
                else:
                    raise RuntimeError("Exceeded max risk-check attempts without a result")
                continue

            if "run backtest" in lower:
                if backtest_attempts < max_backtest_attempts:
                    backtest_attempts += 1
                    _send("run backtest")
                else:
                    raise RuntimeError("Exceeded max backtest attempts without a result")
                continue

            # 6. The strategy is fully specified and the assistant is just confirming.
            #    This is the "ready" state where Aagman sometimes needs a single nudge
            #    to actually execute. Send "run backtest" once, then wait.
            ready_phrases = [
                "ready for backtesting",
                "fully specified",
                "compiling directly",
                "strategy is complete",
                "ready to run",
            ]
            if any(p in lower for p in ready_phrases):
                if backtest_attempts < max_backtest_attempts:
                    backtest_attempts += 1
                    result.add_log("Strategy appears ready; nudging to run backtest")
                    _send("run backtest")
                else:
                    raise RuntimeError(
                        f"Assistant kept saying the strategy is ready but never produced a report card. "
                        f"Last assistant message: {latest[:300]}"
                    )
                continue

            # 7. Clarification question.
            question = detect_clarification(browser, expected, error_markers)
            if question:
                if last_question is not None and question.strip() == last_question.strip():
                    question_repeat_count += 1
                    if question_repeat_count > 2:
                        raise RuntimeError(
                            f"Aagman repeated the same clarification {question_repeat_count} times; giving up"
                        )
                    result.add_log("Same clarification still visible; waiting for page update")
                    time.sleep(2)
                    continue

                last_question = question
                question_repeat_count = 0
                result.add_log(f"Detected clarification: {question[:200]}")

                if answer_provider is None:
                    block(question)
                    return result

                answer = answer_provider.get_answer(prompt, question, body, test)
                if not answer:
                    block(question)
                    return result

                result.add_log(f"Answer generated: {answer[:200]}")
                _send(answer)
                continue

            # 8. Unrecognized assistant state; wait rather than spam.
            result.add_log("No actionable response; waiting")
            time.sleep(3)

        # Timeout.
        body = _body_text(browser)
        raise RuntimeError(f"Timeout waiting for backtest report card. Last page text:\n{body[:800]}")

    except Exception as exc:
        fail(str(exc))

    return result
