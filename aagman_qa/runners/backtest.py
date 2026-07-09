import re
import time
from pathlib import Path

from ..browser import Browser
from ..checks import (
    TestResult,
    assert_no_error_texts,
    assert_texts_present,
    capture_failure_screenshot,
    wait_for_any_text,
)
from ..clarifications import detect_clarification
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
    # a fresh backtest chat. We avoid the "New Backtest" sidebar toggle because
    # it switches between the template grid and the workspace instead of opening
    # a new chat reliably.
    browser.open(base_url)
    for _ in range(20):
        if _has_chat_input(browser):
            break
        time.sleep(0.5)


def _has_report_card(browser: Browser, expected: list[str]) -> bool:
    body = browser.eval("document.body.innerText")
    return all(marker in body for marker in expected)


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

    def _body_text() -> str:
        return str(browser.eval("document.body.innerText"))

    def _aagman_is_thinking(body: str) -> bool:
        return any(phrase in body for phrase in [
            "Thinking",
            "Designing your strategy",
            "preparing",
            "Aagman is",
        ])

    def _send_command(command: str, cooldowns: dict[str, float], cooldown_sec: float = 10.0) -> bool:
        """Send a command like 'run risk checks' unless we just sent it."""
        now = time.time()
        if command in cooldowns and now < cooldowns[command]:
            return False
        submit_aagman_prompt(browser, command)
        result.add_log(f"Sent: {command}")
        cooldowns[command] = now + cooldown_sec
        return True

    try:
        _navigate_to_backtest(browser, base_url)
        result.add_log("Navigated to Backtest")

        # Submit the initial prompt. From the backtest home this creates a fresh chat.
        submit_aagman_prompt(browser, prompt)
        result.add_log(f"Submitted prompt: {prompt[:80]}...")

        # Single conversation loop: keep polling for result markers, command prompts,
        # errors, and clarification questions until timeout.
        deadline = time.time() + timeout
        last_question: str | None = None
        question_repeat_count = 0
        command_cooldowns: dict[str, float] = {}
        failed = False

        while time.time() < deadline:
            body = _body_text()

            # 1. Error markers -> fail immediately and stop sending commands.
            for marker in error_markers:
                if marker in body:
                    failed = True
                    raise RuntimeError(f"Backtest error marker detected: {marker}")

            # 2. Result report card -> pass.
            if all(marker in body for marker in expected):
                assert_no_error_texts(browser, error_markers)
                assert_texts_present(browser, expected)
                result.add_log("Report card markers found")
                result.duration_sec = round(time.time() - start, 2)
                return result

            # 3. Aagman asked us to run the backtest.
            if not failed and "run backtest" in body:
                _send_command("run backtest", command_cooldowns)
                time.sleep(1)
                continue

            # 4. Aagman asked us to run risk checks.
            if not failed and "run risk checks" in body:
                _send_command("run risk checks", command_cooldowns)
                time.sleep(1)
                continue

            # 5. Aagman is still processing the last message — don't treat the
            #    still-visible question as a new clarification.
            if _aagman_is_thinking(body):
                time.sleep(2)
                continue

            # 6. Aagman asked a clarification question.
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
                submit_aagman_prompt(browser, answer)
                result.add_log(f"Submitted answer: {answer[:80]}...")
                time.sleep(1)
                continue

            # Nothing actionable yet; wait and poll again.
            time.sleep(1)

        # Timeout.
        body = _body_text()
        raise RuntimeError(f"Timeout waiting for backtest report card. Last page text:\n{body[:800]}")

    except Exception as exc:
        fail(str(exc))

    return result
