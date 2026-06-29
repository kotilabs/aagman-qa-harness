import time
from pathlib import Path

from ..browser import Browser
from ..checks import (
    TestResult,
    assert_no_error_texts,
    capture_failure_screenshot,
    wait_for_any_text,
)
from ..interactions import submit_aagman_prompt


def _navigate_to_research(browser: Browser, base_url: str) -> None:
    body = browser.eval("document.body.innerText")
    if "New Screener" in body or "New Research" in body or ("Research" in body and "Screener" in body):
        return

    # Click the Research nav item in the sidebar without reloading.
    state = browser.state()
    idx = None
    for line in state.splitlines():
        if "title=Research" in line or "aria-label=Research" in line or "title=Screener" in line:
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
    /research|screener/i.test(e.getAttribute('title') || '') ||
    /research|screener/i.test(e.getAttribute('aria-label') || '')
  );
  if (el) { el.click(); return "OK"; }
  return "NO_RESEARCH_NAV";
})()
""")
    time.sleep(3)


def _start_new_screener_chat(browser: Browser) -> None:
    state = browser.state()
    idx = None
    for line in state.splitlines():
        if "New Screener" in line or "New Research" in line or "New screen" in line:
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
    /new screener|new research|new screen/i.test(b.textContent.trim())
  );
  if (btn) { btn.click(); return "OK"; }
  return "NO_BTN";
})()
""")
    time.sleep(2)


def run(browser: Browser, base_url: str, test: dict, artifact_dir: Path) -> TestResult:
    test_id = test["id"]
    prompt = test["prompt"]
    success_markers = test.get("success_markers", ["Found", "matches", "Symbol", "LTP", "Results"])
    error_markers = test.get("error_markers", [
        "Something went wrong",
        "failed to fetch",
        "error occurred",
        "Unable to process",
    ])
    timeout = test.get("timeout", 90)

    result = TestResult(id=test_id, status="PASS", duration_sec=0.0, prompt=prompt)
    start = time.time()

    try:
        _navigate_to_research(browser, base_url)
        result.add_log("Navigated to Research")

        _start_new_screener_chat(browser)
        result.add_log("Started a new screener chat")

        submit_aagman_prompt(browser, prompt)
        result.add_log(f"Submitted prompt: {prompt[:80]}...")

        found = wait_for_any_text(browser, success_markers + error_markers, timeout=timeout, interval=1.0)
        if found in error_markers:
            raise RuntimeError(f"Research error marker detected: {found}")
        if found is None:
            raise RuntimeError("Timeout waiting for screener results")

        assert_no_error_texts(browser, error_markers)
        result.add_log(f"Success marker found: {found}")
        result.duration_sec = round(time.time() - start, 2)
    except Exception as exc:
        result.status = "FAIL"
        result.message = str(exc)
        result.duration_sec = round(time.time() - start, 2)
        ss_path = artifact_dir / f"{test_id}_fail.png"
        try:
            capture_failure_screenshot(browser, ss_path)
            result.add_screenshot(ss_path)
            result.add_log(f"Screenshot captured: {ss_path}")
        except Exception as ss_exc:
            result.add_log(f"Screenshot failed: {ss_exc}")

    return result
