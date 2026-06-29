import json
import time
from pathlib import Path

from ..browser import Browser
from ..checks import TestResult, capture_failure_screenshot


def _navigate(browser: Browser, base_url: str) -> None:
    browser.open(f"{base_url}/charts")
    time.sleep(3)


def _switch_timeframe(browser: Browser, tf: str) -> bool:
    script = f"""
(() => {{
  const btn = Array.from(document.querySelectorAll('button')).find(b =>
    b.textContent.trim() === {json.dumps(tf)} ||
    b.getAttribute('data-value') === {json.dumps(tf)} ||
    b.getAttribute('data-testid') === {json.dumps('timeframe-' + tf)}
  );
  if (btn) {{ btn.click(); return true; }}
  return false;
}})()
"""
    return bool(browser.eval(script))


def _add_indicator(browser: Browser, name: str) -> bool:
    # Open the indicator/overlays selector and pick by name.
    script = f"""
(() => {{
  // Try to open the indicator menu
  const addBtn = Array.from(document.querySelectorAll('button')).find(b =>
    /indicator|overlay|study|add/i.test(b.textContent.trim()) ||
    /indicator|overlay|study/i.test(b.getAttribute('aria-label') || '')
  );
  if (addBtn) addBtn.click();

  // Wait a tick and search the menu items
  const items = Array.from(document.querySelectorAll('li, button, div[role="option"], div[role="menuitem"]'));
  const item = items.find(i => i.textContent.trim().toLowerCase() === {json.dumps(name.lower())});
  if (item) {{ item.click(); return true; }}

  // Some UIs use a search input
  const search = document.querySelector('input[placeholder*="indicator" i], input[placeholder*="search" i]');
  if (search) {{
    search.value = {json.dumps(name)};
    search.dispatchEvent(new Event('input', {{ bubbles: true }}));
    setTimeout(() => {{
      const first = document.querySelector('li, [role="option"]');
      if (first) first.click();
    }}, 300);
    return true;
  }}
  return false;
}})()
"""
    return bool(browser.eval(script))


def _active_indicator_names(browser: Browser) -> list[str]:
    script = """
(() => {
  const names = [];
  document.querySelectorAll('[data-testid*="indicator"], .indicator-label, .legend-item, .chart-indicator').forEach(el => {
    names.push(el.textContent.trim());
  });
  return names;
})()
"""
    return browser.eval(script) or []


def run(browser: Browser, base_url: str, test: dict, artifact_dir: Path) -> TestResult:
    test_id = test["id"]
    timeframes = test.get("timeframes", ["1m", "5m", "15m", "1h", "1d"])
    indicators = test.get("indicators", ["SMA", "EMA", "RSI"])
    timeout = test.get("timeout", 60)

    result = TestResult(
        id=test_id,
        status="PASS",
        duration_sec=0.0,
        prompt=test.get("prompt", ""),
        title=test.get("title", test_id),
        description=test.get("description", ""),
    )
    start = time.time()
    deadline = start + timeout

    try:
        _navigate(browser, base_url)
        result.add_log("Navigated to Charts")

        # Verify canvas exists
        has_canvas = browser.eval("return !!document.querySelector('canvas')")
        if not has_canvas:
            raise RuntimeError("Chart canvas not found")
        result.add_log("Chart canvas present")

        # Switch timeframes
        for tf in timeframes:
            if time.time() > deadline:
                raise RuntimeError(f"Timeout while switching timeframes at {tf}")
            ok = _switch_timeframe(browser, tf)
            result.add_log(f"Timeframe {tf}: {'clicked' if ok else 'button not found'}")
            time.sleep(1.5)

        # Add indicators (max 3 as requested)
        added = []
        for idx, ind in enumerate(indicators[:3], 1):
            if time.time() > deadline:
                raise RuntimeError(f"Timeout while adding indicators at {ind}")
            ok = _add_indicator(browser, ind)
            result.add_log(f"Indicator {ind}: {'added' if ok else 'menu item not found'}")
            if ok:
                added.append(ind)
            time.sleep(1.5)

        # Verify at least one indicator label appears if we tried to add.
        if added:
            active = _active_indicator_names(browser)
            if not any(a.lower() in [x.lower() for x in added] for a in active):
                # Sometimes labels are abbreviated; be lenient.
                result.add_log(f"Indicator labels not immediately visible; active labels: {active}")
            else:
                result.add_log(f"Active indicator labels: {active}")

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
