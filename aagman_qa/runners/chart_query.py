"""Runner for natural-language chart/data queries that need screenshot verification."""
from __future__ import annotations

import time
from pathlib import Path

from ..browser import Browser
from ..checks import TestResult, capture_failure_screenshot
from ..conversation import scroll_chat_to_bottom
from ..interactions import submit_aagman_prompt
from ..vision import VisionError, verify_chart


def _navigate_to_research(browser: Browser, base_url: str) -> None:
    body = browser.eval("document.body.innerText")
    if "New Screener" in body or "New Research" in body or ("Research" in body and "Screener" in body):
        return

    state = browser.state()
    idx = None
    import re
    for line in state.splitlines():
        if "title=Research" in line or "aria-label=Research" in line or "title=Screener" in line:
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


def _start_new_research_chat(browser: Browser) -> None:
    state = browser.state()
    idx = None
    import re
    for line in state.splitlines():
        if "New Screener" in line or "New Research" in line or "New screen" in line:
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


# Phrases Aagman uses when it is about to render a chart/widget.
_CHART_ACKNOWLEDGEMENTS = [
    "here's your chart",
    "here is your chart",
    "chart for",
    "plot for",
    "price chart",
    "showing the chart",
    "candlestick chart",
    "below is the chart",
]


def _looks_like_chart_ack(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in _CHART_ACKNOWLEDGEMENTS)




def _body_text(browser: Browser) -> str:
    return str(browser.eval("document.body.innerText"))


def _wait_for_chart_ack(
    browser: Browser,
    prompt: str,
    timeout: float,
    poll_interval: float = 1.0,
) -> str | None:
    """Wait until Aagman acknowledges the chart request in the page text.

    We poll the full body text instead of extracting structured chat messages,
    because Aagman's chart responses mutate the DOM heavily while rendering and
    the generic message extraction fails to classify them.
    """
    deadline = time.time() + timeout
    prompt_seen = False

    while time.time() < deadline:
        scroll_chat_to_bottom(browser)
        body = _body_text(browser)

        # Make sure our prompt actually made it into the page.
        if prompt.strip() in body:
            prompt_seen = True

        if prompt_seen:
            lowered = body.lower()
            for phrase in _CHART_ACKNOWLEDGEMENTS:
                if phrase in lowered:
                    # Return the sentence-ish snippet around the acknowledgement.
                    idx = lowered.find(phrase)
                    start = body.rfind("\n", 0, idx) + 1
                    end = body.find("\n", idx)
                    if end == -1:
                        end = len(body)
                    return body[start:end].strip()

        time.sleep(poll_interval)

    return None


def _screenshot_latest_message(browser: Browser, path: Path) -> Path:
    """Scroll the chat to the bottom and capture the latest reply area."""
    scroll_chat_to_bottom(browser)
    time.sleep(0.5)
    path.parent.mkdir(parents=True, exist_ok=True)
    browser.screenshot(path, full_page=False)
    return path


def run(
    browser: Browser,
    base_url: str,
    test: dict,
    artifact_dir: Path,
    answer_provider=None,
) -> TestResult:
    test_id = test["id"]
    prompt = test["prompt"]
    symbol = test.get("symbol")
    description = test.get("description", "price chart")
    timeout = test.get("timeout", 120)
    vision_timeout = test.get("vision_timeout", 60)

    result = TestResult(
        id=test_id,
        status="PASS",
        duration_sec=0.0,
        prompt=prompt,
        title=test.get("title", test_id),
        description=test.get("description", ""),
    )
    start = time.time()

    def fail(message: str) -> None:
        result.status = "FAIL"
        result.message = message
        result.duration_sec = round(time.time() - start, 2)
        ss_path = artifact_dir / f"{test_id}_fail.png"
        try:
            capture_failure_screenshot(browser, ss_path)
            result.add_screenshot(ss_path)
            result.add_log(f"Failure screenshot: {ss_path}")
        except Exception as ss_exc:
            result.add_log(f"Screenshot failed: {ss_exc}")

    try:
        _navigate_to_research(browser, base_url)
        result.add_log("Navigated to Research")

        _start_new_research_chat(browser)
        result.add_log("Started new research chat")

        submit_aagman_prompt(browser, prompt)
        result.add_log(f"Submitted prompt: {prompt[:80]}...")

        assistant_text = _wait_for_chart_ack(
            browser,
            prompt=prompt,
            timeout=timeout,
        )
        if not assistant_text:
            raise RuntimeError("Timed out waiting for assistant response")
        result.add_log(f"Assistant response: {assistant_text[:160]}...")

        # Allow the chart widget time to render after Aagman acknowledges it.
        render_wait = 6.0 if _looks_like_chart_ack(assistant_text) else 2.0
        result.add_log(f"Waiting {render_wait}s for chart render")
        time.sleep(render_wait)

        # Capture screenshot of the latest reply and verify with vision.
        screenshot_path = artifact_dir / f"{test_id}_chart.png"
        _screenshot_latest_message(browser, screenshot_path)
        result.add_screenshot(screenshot_path)
        result.add_log(f"Screenshot captured: {screenshot_path}")

        try:
            verdict = verify_chart(
                screenshot_path,
                symbol=symbol,
                description=description,
                timeout=vision_timeout,
            )
        except VisionError as exc:
            raise RuntimeError(f"Vision verification failed: {exc}") from exc

        result.add_log(f"Vision verdict: {verdict['answer']} — {verdict['reason']}")

        # Aagman's chart rendering is flaky; if vision says no chart, wait a bit
        # longer, screenshot again, and ask vision one more time.
        if verdict["answer"] != "yes" and _looks_like_chart_ack(assistant_text):
            retry_wait = 10.0
            result.add_log(f"Chart not visible, waiting {retry_wait}s and retrying")
            time.sleep(retry_wait)
            _screenshot_latest_message(browser, screenshot_path)
            try:
                verdict = verify_chart(
                    screenshot_path,
                    symbol=symbol,
                    description=description,
                    timeout=vision_timeout,
                )
            except VisionError as exc:
                raise RuntimeError(f"Vision verification failed on retry: {exc}") from exc
            result.add_log(f"Vision verdict (retry): {verdict['answer']} — {verdict['reason']}")

        if verdict["answer"] != "yes":
            raise RuntimeError(f"Vision verification failed: {verdict['reason']}")

        result.duration_sec = round(time.time() - start, 2)
    except Exception as exc:
        fail(str(exc))

    return result
