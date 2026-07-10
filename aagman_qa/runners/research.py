import re
import time
from pathlib import Path

from ..browser import Browser
from ..checks import (
    TestResult,
    assert_no_error_texts,
    capture_failure_screenshot,
)
from ..clarifications import detect_clarification
from ..interactions import submit_aagman_prompt


# Aagman screener result signals. We look for these inside the main workspace,
# not anywhere in the DOM, so the sidebar "Results" tab / history can't fake a pass.
# Aagman renders results in the "Results" tab using phrasing like "1 stocks match:".
_RESULT_RE = re.compile(r"Found\s+\d+\s+matches?|\d+\s+stocks?\s+match", re.IGNORECASE)
_ZERO_RESULT_RE = re.compile(r"Found\s+0\s+matches?|No\s+matches?|0\s+stocks?\s+match", re.IGNORECASE)

# Loading states that mean Aagman has not finished yet.
_LOADING_PHRASES = [
    "Scanning the market",
    "Thinking",
    "Loading",
    "Please wait",
    "Hang tight",
]

# Markers that prove an actual result (not just a tab label) is present.
_RESULT_MARKERS = ["Found", "matches", "Symbol", "LTP"]


def _main_text(browser: Browser) -> str:
    """Return the text of the main workspace area, ignoring nav/header noise."""
    return str(
        browser.eval(
            """
(() => {
  const main = document.querySelector('main')
    || document.querySelector('[class*="Workspace"]')
    || document.querySelector('[class*="workspace"]')
    || document.body;
  return main.innerText || '';
})()
"""
        )
    )


def _is_on_research(browser: Browser) -> bool:
    url = browser.current_url()
    return isinstance(url, str) and "/screener" in url


def _navigate_to_research(browser: Browser, base_url: str) -> None:
    """Make sure the browser is on the Research workspace without reloading."""
    if _is_on_research(browser):
        return

    # If we are on the login page, a reload is unavoidable.
    url = browser.current_url()
    if isinstance(url, str) and "/login" in url:
        browser.open(base_url)
        time.sleep(3)
        return

    # Click the Research sidebar item.
    state = browser.state()
    idx = None
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


def _start_new_screener_chat(browser: Browser) -> None:
    """Click New Research so each test gets a fresh chat."""
    state = browser.state()
    idx = None
    for line in state.splitlines():
        if "New Research" in line or "New Screener" in line or "New screen" in line:
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


def _is_loading(text: str) -> bool:
    return any(phrase in text for phrase in _LOADING_PHRASES)


def _has_result(text: str) -> bool:
    if _RESULT_RE.search(text):
        return True
    if _ZERO_RESULT_RE.search(text):
        return True
    # Fallback: a rendered table with screener-specific headers.
    if "Symbol" in text and ("LTP" in text or "RSI" in text or "Close" in text):
        return True
    return False


def _activate_results_tab(browser: Browser) -> None:
    """Switch to the Results tab so we can wait for the actual results table."""
    browser.eval("""
(() => {
  const tabs = Array.from(document.querySelectorAll('button, [role="tab"]'));
  const resultsTab = tabs.find(t => /Results/i.test(t.textContent.trim()));
  if (resultsTab) { resultsTab.click(); return "clicked"; }
  return "none";
})()
""")


def _activate_chat_tab(browser: Browser) -> None:
    """Make sure the Chat tab is active so the input box is available."""
    browser.eval("""
(() => {
  const tabs = Array.from(document.querySelectorAll('button, [role="tab"]'));
  const chatTab = tabs.find(t => /Chat/i.test(t.textContent.trim()));
  if (chatTab) { chatTab.click(); return "clicked"; }
  return "none";
})()
""")


def _last_ai_message(browser: Browser) -> dict | None:
    """Return the text and row count of the most recent assistant message bubble."""
    # browser-use eval returns Python-repr strings for objects, so return an array.
    out = browser.eval("""
(() => {
  const bubbles = Array.from(document.querySelectorAll('[class*="aiMessage"]'));
  const last = bubbles[bubbles.length - 1];
  if (!last) return null;
  const rows = last.querySelectorAll('tr, [role="row"]');
  const dataRows = Array.from(rows).filter(r => r.querySelectorAll('td').length >= 2);
  return [last.innerText || '', rows.length, dataRows.length];
})()
""")
    if out is None:
        return None
    if isinstance(out, list) and len(out) == 3:
        return {"text": out[0], "totalRows": out[1], "dataRows": out[2]}
    return None


def _ai_message_has_result(ai: dict | None) -> bool:
    if not ai:
        return False
    if _has_result(ai["text"]):
        return True
    # A rendered results table inside the assistant bubble.
    return ai.get("dataRows", 0) >= 1


def _wait_for_result(
    browser: Browser,
    error_markers: list[str],
    timeout: float,
    answer_provider=None,
    test: dict | None = None,
    prompt: str = "",
) -> str | None:
    """Wait until Aagman finishes loading and returns a result, error, or clarification.

    Aagman usually renders screener results in the "Results" tab while the chat
    bubble keeps showing "Scanning the market...". So we first give the chat a few
    seconds, then switch to the Results tab and wait for both a result phrase and a
    real data table there.

    Returns:
      - "result" if a result phrase/table is detected.
      - "error:<marker>" if an error marker is detected.
      - "clarification:<question>" if Aagman asks a follow-up question.
      - None on timeout.
    """
    deadline = time.time() + timeout
    start = time.time()
    last_text = ""
    stable_ticks = 0
    clarification_checked = False
    results_tab_activated = False

    while time.time() < deadline:
        text = _main_text(browser)
        lower = text.lower()

        for err in error_markers:
            if err.lower() in lower:
                return f"error:{err}"

        # Fast path: result rendered directly in the latest chat bubble.
        ai = _last_ai_message(browser)
        if _ai_message_has_result(ai):
            return "result"

        if _is_loading(text):
            stable_ticks = 0
            clarification_checked = False
            # Once loading has been visible for a few seconds, switch to the Results tab.
            # Aagman renders the actual results table there even though the chat bubble
            # may keep saying "Scanning the market...".
            if not results_tab_activated and (time.time() - start) >= 5:
                _activate_results_tab(browser)
                results_tab_activated = True
                time.sleep(0.5)
        else:
            if results_tab_activated:
                # On the Results tab we require both a result phrase and a data row.
                if _has_result(text) and _results_table_present(browser):
                    return "result"
            if text == last_text:
                stable_ticks += 1
                # Only check for clarification once the page has been stable for a bit.
                if stable_ticks >= 6 and not clarification_checked and answer_provider is not None:
                    clarification_checked = True
                    question = detect_clarification(browser, _RESULT_MARKERS, error_markers)
                    if question:
                        return f"clarification:{question}"
            else:
                stable_ticks = 0
                clarification_checked = False

        last_text = text
        time.sleep(0.5)

    return None


def _results_table_present(browser: Browser) -> bool:
    """Check that the main workspace actually contains a data table or result cards."""
    return bool(
        browser.eval(
            """
(() => {
  const main = document.querySelector('main')
    || document.querySelector('[class*="Workspace"]')
    || document.querySelector('[class*="workspace"]')
    || document.body;
  const rows = main.querySelectorAll('tr, [role="row"]');
  const dataRows = Array.from(rows).filter(r => r.querySelectorAll('td').length >= 2);
  const cards = main.querySelectorAll('[class*="result"], [data-testid*="result"]');
  return dataRows.length >= 1 || cards.length >= 1;
})()
"""
        )
    )


def _capture_screenshot(browser: Browser, path: Path, full_page: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        browser.eval("""
(() => {
  const all = Array.from(document.querySelectorAll('*'));
  const el = all.filter(e => e.scrollHeight > e.clientHeight + 10)
                .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  if (el) el.scrollTop = el.scrollHeight;
  return 'OK';
})()
""")
    except Exception:
        pass
    browser.screenshot(path, full_page=full_page)
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
    error_markers = test.get(
        "error_markers",
        [
            "Something went wrong",
            "failed to fetch",
            "error occurred",
            "Unable to process",
        ],
    )
    timeout = test.get("timeout", 300)

    result = TestResult(id=test_id, status="PASS", duration_sec=0.0, prompt=prompt)
    start = time.time()

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

    try:
        _navigate_to_research(browser, base_url)
        result.add_log("On Research workspace")

        _start_new_screener_chat(browser)
        result.add_log("Started a new research chat")

        # Ensure the Chat tab is active before typing; the input may be hidden on the Results tab.
        _activate_chat_tab(browser)
        time.sleep(0.5)

        current_text = prompt
        last_question: str | None = None
        outcome: str | None = None

        for round_num in range(3):
            submit_aagman_prompt(browser, current_text)
            result.add_log(f"Submitted prompt (round {round_num + 1}): {current_text[:80]}...")

            outcome = _wait_for_result(
                browser,
                error_markers,
                timeout=timeout,
                answer_provider=answer_provider,
                test=test,
                prompt=prompt,
            )

            if outcome == "result":
                result.add_log("Result phrase/table detected")
                break

            if outcome and outcome.startswith("error:"):
                raise RuntimeError(f"Research error marker detected: {outcome.split(':', 1)[1]}")

            if outcome and outcome.startswith("clarification:"):
                question = outcome.split(":", 1)[1]
                if last_question is not None and question.strip() == last_question.strip():
                    result.add_log("Same clarification still visible; waiting for page update")
                    break
                last_question = question
                result.add_log(f"Detected clarification: {question[:200]}")
                if answer_provider is None:
                    block(question)
                    return result
                answer = answer_provider.get_answer(prompt, question, _main_text(browser), test)
                if answer:
                    result.add_log(f"Answer generated: {answer[:200]}")
                    current_text = answer
                    continue
                else:
                    block(question)
                    return result

            if outcome is None:
                raise RuntimeError("Timeout waiting for screener results")

        if outcome != "result":
            raise RuntimeError("Aagman did not return a result")

        assert_no_error_texts(browser, error_markers)

        # Extra safety: make sure the result actually rendered in the chat bubble or
        # in the Results tab (whichever the wait loop used).
        has_chat_result = _ai_message_has_result(_last_ai_message(browser))
        has_results_tab_result = _has_result(_main_text(browser)) and _results_table_present(browser)
        if not (has_chat_result or has_results_tab_result):
            result.add_log("Result signal present but content still loading; waiting a few seconds")
            time.sleep(5)
            has_chat_result = _ai_message_has_result(_last_ai_message(browser))
            has_results_tab_result = _has_result(_main_text(browser)) and _results_table_present(browser)
            if not (has_chat_result or has_results_tab_result):
                raise RuntimeError("Result signal found but no rendered result in chat or Results tab")

        result.add_log("Results rendered and table/cards stable")

        ss_path = artifact_dir / f"{test_id}_pass.png"
        _capture_screenshot(browser, ss_path)
        result.add_screenshot(ss_path)
        result.add_log(f"Pass screenshot captured: {ss_path}")

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
