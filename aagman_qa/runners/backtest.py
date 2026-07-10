import json
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
    body = str(browser.eval("document.body.innerText")).lower()
    return all(marker.lower() in body for marker in expected)


def run(
    browser: Browser,
    base_url: str,
    test: dict,
    artifact_dir: Path,
    answer_provider=None,
) -> TestResult:
    test_id = test["id"]
    prompt = test["prompt"]
    expected = test.get("expected_contains", ["Total PnL", "Max Drawdown", "Win Rate"])
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


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _goto_workspace(browser: Browser, url: str, timeout: float = 15.0) -> None:
    browser.eval(f"window.location.href = {json.dumps(url)};")
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = browser.current_url()
        if isinstance(current, str) and url in current:
            return
        time.sleep(0.5)


def _wait_for_workspace_url(browser: Browser, deadline_sec: float = 15.0) -> str:
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        url = browser.current_url()
        if isinstance(url, str) and _UUID_RE.search(url):
            return url
        time.sleep(0.5)
    url = browser.current_url()
    return url if isinstance(url, str) else ""


def _body_text(browser: Browser) -> str:
    return str(browser.eval("document.body.innerText"))


def _aagman_is_thinking(browser: Browser) -> bool:
    body = _body_text(browser)
    return any(phrase in body for phrase in [
        "Thinking",
        "Designing your strategy",
        "preparing",
        "Aagman is",
    ])


def _send_command_if_prompted(browser: Browser, command_cooldowns: dict[str, float]) -> bool:
    """If the workspace is asking for a command, send it once. Returns True if a command was sent."""
    body = _body_text(browser)
    now = time.time()

    # Order matters: risk checks first, then run backtest.
    if "run risk checks" in body.lower():
        if "run risk checks" not in command_cooldowns or now >= command_cooldowns["run risk checks"]:
            submit_aagman_prompt(browser, "run risk checks")
            command_cooldowns["run risk checks"] = now + 10
            return True

    if "run backtest" in body.lower():
        if "run backtest" not in command_cooldowns or now >= command_cooldowns["run backtest"]:
            submit_aagman_prompt(browser, "run backtest")
            command_cooldowns["run backtest"] = now + 10
            return True

    return False


def _advance_and_wait_for_report(
    browser: Browser,
    expected: list[str],
    error_markers: list[str],
    timeout: float,
) -> bool:
    """Send any pending commands and wait for the backtest report card."""
    deadline = time.time() + timeout
    command_cooldowns: dict[str, float] = {}

    while time.time() < deadline:
        body = _body_text(browser)
        lower = body.lower()

        for marker in error_markers:
            if marker.lower() in lower:
                raise RuntimeError(f"Backtest error marker detected: {marker}")

        if all(marker.lower() in body.lower() for marker in expected):
            return True

        if _aagman_is_thinking(browser):
            time.sleep(2)
            continue

        if _send_command_if_prompted(browser, command_cooldowns):
            time.sleep(1)
            continue

        time.sleep(1)

    return False


def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _send_command_once(browser: Browser, command: str, last_sent: dict[str, float], cooldown: float = 10.0) -> bool:
    """Send a command if it hasn't been sent recently."""
    now = time.time()
    if last_sent.get(command, 0) + cooldown <= now:
        submit_aagman_prompt(browser, command)
        last_sent[command] = now
        return True
    return False


def _issue_command_to_all(
    items: list[dict], browser: Browser, command: str, deadline_sec: float = 60
) -> None:
    """Visit every workspace and send the given command if the workspace is asking for it."""
    for item in items:
        if item["result"].status != "PASS":
            continue
        workspace_url = item["workspace_url"]
        try:
            if isinstance(workspace_url, str) and workspace_url:
                _goto_workspace(browser, workspace_url)
                time.sleep(2)

            last_sent: dict[str, float] = {}
            deadline = time.time() + deadline_sec
            while time.time() < deadline:
                body = _body_text(browser).lower()
                if command in body:
                    _send_command_once(browser, command, last_sent)
                    break
                if all(marker.lower() in body for marker in item["expected"]):
                    break
                if not _aagman_is_thinking(browser):
                    break
                time.sleep(1)
        except Exception as exc:
            item["result"].status = "FAIL"
            item["result"].message = str(exc)


def run_batch(
    browser: Browser,
    base_url: str,
    tests: list[dict],
    artifact_dir: Path,
    answer_provider=None,
    batch_size: int = 8,
    risk_delay: float = 120,
    backtest_delay: float = 180,
    result_delay: float = 240,
    check_timeout: float = 120,
) -> list[TestResult]:
    """Run backtest prompts in phased batches.

    1. Submit all prompts.
    2. Wait for Aagman to ask for "run risk checks", then send it to every workspace.
    3. Wait for Aagman to ask for "run backtest", then send it to every workspace.
    4. Wait for the report card to render, then check every workspace.
    """
    if not tests:
        return []

    all_results: list[TestResult] = []

    for chunk_idx, chunk in enumerate(_chunks(tests, batch_size), start=1):
        chunk_results: list[TestResult] = []
        items: list[dict] = []

        print(f"  Backtest batch {chunk_idx}: submitting {len(chunk)} prompts...")

        for test in chunk:
            test_id = test["id"]
            prompt = test["prompt"]
            expected = test.get("expected_contains", ["Total PnL", "Max Drawdown", "Win Rate"])
            error_markers = test.get(
                "error_markers",
                [
                    "Cannot run backtest",
                    "Backtest failed",
                    "Unable to run backtest",
                    "No data available",
                ],
            )
            result = TestResult(id=test_id, status="PASS", duration_sec=0.0, prompt=prompt)
            start = time.time()

            try:
                _navigate_to_backtest(browser, base_url)
                submit_aagman_prompt(browser, prompt)
                workspace_url = _wait_for_workspace_url(browser, deadline_sec=15)
                result.add_log(f"Batch {chunk_idx}: prompt submitted; workspace={workspace_url}")

                items.append({
                    "test": test,
                    "result": result,
                    "start": start,
                    "workspace_url": workspace_url,
                    "expected": expected,
                    "error_markers": error_markers,
                })
                chunk_results.append(result)
            except Exception as exc:
                result.status = "FAIL"
                result.message = str(exc)
                result.duration_sec = round(time.time() - start, 2)
                chunk_results.append(result)

        submitted = [item for item in items if item["result"].status == "PASS"]
        print(
            f"⏳ Backtest batch {chunk_idx}: {len(submitted)}/{len(chunk)} submitted; "
            f"waiting {risk_delay}s before sending 'run risk checks'..."
        )
        time.sleep(risk_delay)
        _issue_command_to_all(items, browser, "run risk checks", deadline_sec=60)
        print(f"  Sent 'run risk checks' where needed; waiting {backtest_delay}s...")
        time.sleep(backtest_delay)
        _issue_command_to_all(items, browser, "run backtest", deadline_sec=60)
        print(f"  Sent 'run backtest' where needed; waiting {result_delay}s for reports...")
        time.sleep(result_delay)

        for item in items:
            test = item["test"]
            result = item["result"]
            start = item["start"]
            workspace_url = item["workspace_url"]
            expected = item["expected"]
            error_markers = item["error_markers"]
            test_id = test["id"]

            try:
                if result.status != "PASS":
                    continue

                if isinstance(workspace_url, str) and workspace_url:
                    _goto_workspace(browser, workspace_url)
                    time.sleep(2)

                if _advance_and_wait_for_report(
                    browser,
                    expected,
                    error_markers,
                    timeout=check_timeout,
                ):
                    result.add_log("Backtest report card detected after batch settle")
                    assert_no_error_texts(browser, error_markers)
                    ss_path = artifact_dir / f"{test_id}_pass.png"
                    try:
                        from ..checks import capture_failure_screenshot
                        capture_failure_screenshot(browser, ss_path)
                        result.add_screenshot(ss_path)
                    except Exception as ss_exc:
                        result.add_log(f"Pass screenshot failed: {ss_exc}")
                    result.status = "PASS"
                else:
                    ss_path = artifact_dir / f"{test_id}_timeout.png"
                    try:
                        from ..checks import capture_failure_screenshot
                        capture_failure_screenshot(browser, ss_path)
                        result.add_screenshot(ss_path)
                    except Exception as ss_exc:
                        result.add_log(f"Timeout screenshot failed: {ss_exc}")
                    raise RuntimeError("Timeout waiting for backtest report card after batch settle")
            except Exception as exc:
                if result.status == "PASS":
                    result.status = "FAIL"
                    result.message = str(exc)
                ss_path = artifact_dir / f"{test_id}_fail.png"
                try:
                    capture_failure_screenshot(browser, ss_path)
                    result.add_screenshot(ss_path)
                except Exception as ss_exc:
                    result.add_log(f"Fail screenshot failed: {ss_exc}")
            finally:
                result.duration_sec = round(time.time() - start, 2)

        all_results.extend(chunk_results)

    return all_results
