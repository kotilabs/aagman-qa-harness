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
from ..interactions import submit_aagman_prompt


def _navigate_to_backtest(browser: Browser, base_url: str) -> None:
    body = browser.eval("document.body.innerText")
    # Already on backtest if the page has the backtest chat/workspace context.
    if "New Backtest" in body or ("Backtest" in body and "Strategy" in body):
        return

    # Click the Backtest nav item in the sidebar without reloading the page.
    state = browser.state()
    idx = None
    for line in state.splitlines():
        if "title=Backtest" in line or "aria-label=Backtest" in line:
            import re
            m = re.search(r"\[(\d+)\]", line)
            if m:
                idx = int(m.group(1))
                break

    if idx is not None:
        browser.click(idx)
    else:
        browser.eval("""
(() => {
  const el = Array.from(document.querySelectorAll('a, button')).find(e =>
    /backtest/i.test(e.getAttribute('title') || '') ||
    /backtest/i.test(e.getAttribute('aria-label') || '')
  );
  if (el) { el.click(); return "OK"; }
  return "NO_BACKTEST_NAV";
})()
""")
    time.sleep(3)


def _start_new_backtest_chat(browser: Browser) -> None:
    # Click "New Backtest" to start a fresh chat for each prompt.
    state = browser.state()
    idx = None
    for line in state.splitlines():
        if "New Backtest" in line:
            import re
            m = re.search(r"\[(\d+)\]", line)
            if m:
                idx = int(m.group(1))
                break
    if idx is not None:
        browser.click(idx)
    else:
        browser.eval("""
(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b =>
    /new backtest/i.test(b.textContent.trim())
  );
  if (btn) { btn.click(); return "OK"; }
  return "NO_BTN";
})()
""")
    time.sleep(2)


def _has_report_card(browser: Browser, expected: list[str]) -> bool:
    body = browser.eval("document.body.innerText")
    return all(marker in body for marker in expected)


def run(browser: Browser, base_url: str, test: dict, artifact_dir: Path) -> TestResult:
    test_id = test["id"]
    prompt = test["prompt"]
    expected = test.get("expected_contains", ["TOTAL PnL", "MAX DRAWDOWN", "WIN RATE"])
    error_markers = test.get("error_markers", [
        "Cannot run backtest",
        "Backtest failed",
        "Unable to run backtest",
        "No data available",
    ])
    timeout = test.get("timeout", 180)

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

    try:
        _navigate_to_backtest(browser, base_url)
        result.add_log("Navigated to Backtest")

        _start_new_backtest_chat(browser)
        result.add_log("Started a new backtest chat")

        submit_aagman_prompt(browser, prompt)
        result.add_log(f"Submitted prompt: {prompt[:80]}...")

        # Stage 1: wait for strategy confirmation / risk-check prompt / result.
        markers = ["run risk checks", "run backtest"] + expected + error_markers
        found = wait_for_any_text(browser, markers, timeout=remaining(), interval=1.0)
        if found in error_markers:
            raise RuntimeError(f"Backtest error marker detected: {found}")
        if found == "run risk checks":
            result.add_log("Agent asked to run risk checks")
            submit_aagman_prompt(browser, "run risk checks")
            result.add_log("Sent: run risk checks")
            # Stage 2: wait for backtest run prompt / result.
            markers = ["run backtest"] + expected + error_markers
            found = wait_for_any_text(browser, markers, timeout=remaining(), interval=1.0)
            if found in error_markers:
                raise RuntimeError(f"Backtest error marker detected: {found}")
        if found == "run backtest":
            result.add_log("Agent asked to run backtest")
            submit_aagman_prompt(browser, "run backtest")
            result.add_log("Sent: run backtest")

        # Final wait for report card.
        found = wait_for_any_text(browser, expected + error_markers, timeout=remaining(), interval=1.0)
        if found in error_markers:
            raise RuntimeError(f"Backtest error marker detected: {found}")
        if found is None:
            body = browser.eval("document.body.innerText")
            raise RuntimeError(
                f"Timeout waiting for backtest report card. Last page text:\n{body[:800]}"
            )

        assert_no_error_texts(browser, error_markers)
        assert_texts_present(browser, expected)

        result.add_log(f"Success marker found: {found}")
        result.duration_sec = round(time.time() - start, 2)
    except Exception as exc:
        fail(str(exc))

    return result
