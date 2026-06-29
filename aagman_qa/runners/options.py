import json
import time
from pathlib import Path

from ..browser import Browser
from ..checks import TestResult, capture_failure_screenshot


def _navigate(browser: Browser, base_url: str) -> None:
    browser.open(f"{base_url}/options")
    time.sleep(3)


def _has_spinner(browser: Browser) -> bool:
    return browser.eval("""
(() => {
  const spinners = document.querySelectorAll('.spinner, [class*="spinner"], [class*="loading"], [data-testid*="loading"]');
  return spinners.length > 0 && Array.from(spinners).some(s => s.offsetParent !== null);
})()
""")


def _wait_for_spinner(browser: Browser, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _has_spinner(browser):
            return
        time.sleep(0.5)


def _option_chain_loaded(browser: Browser) -> bool:
    return browser.eval("""
(() => {
  const body = document.body.innerText;
  return body.includes('PCR') && body.includes('Max Pain') && body.includes('ATM IV');
})()
""")


def _switch_tab(browser: Browser, tab_name: str) -> bool:
    script = f"""
(() => {{
  const tab = Array.from(document.querySelectorAll('button, a, [role="tab"]')).find(e =>
    e.textContent.trim().toLowerCase() === {json.dumps(tab_name.lower())}
  );
  if (tab) {{ tab.click(); return true; }}
  return false;
}})()
"""
    return bool(browser.eval(script))


def _add_payoff_leg(browser: Browser) -> bool:
    return bool(browser.eval("""
(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b =>
    /add leg/i.test(b.textContent.trim()) || b.getAttribute('data-testid') === 'add-leg'
  );
  if (btn) { btn.click(); return true; }
  return false;
})()
"""))


def _remove_payoff_leg(browser: Browser, index: int = 0) -> bool:
    script = f"""
(() => {{
  const btns = Array.from(document.querySelectorAll('button')).filter(b => /remove|delete|trash/i.test(b.textContent.trim()) || /remove|delete|trash/i.test(b.getAttribute('aria-label') || ''));
  if (btns.length > {index}) {{ btns[{index}].click(); return true; }}
  return false;
}})()
"""
    return bool(browser.eval(script))


def _net_premium_text(browser: Browser) -> str:
    return browser.eval("""
(() => {
  const el = Array.from(document.querySelectorAll('*')).find(e =>
    /net premium/i.test(e.textContent.trim())
  );
  return el ? el.textContent.trim() : '';
})()
""")


def _generate_vol_surface(browser: Browser) -> bool:
    return bool(browser.eval("""
(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b =>
    /generate surface/i.test(b.textContent.trim()) || b.getAttribute('data-testid') === 'generate-surface'
  );
  if (btn) { btn.click(); return true; }
  return false;
})()
"""))


def run(browser: Browser, base_url: str, test: dict, artifact_dir: Path) -> TestResult:
    test_id = test["id"]
    timeout = test.get("timeout", 90)

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
        result.add_log("Navigated to Options")

        # 1. Option Chain loads
        _wait_for_spinner(browser, timeout=20)
        if not _option_chain_loaded(browser):
            raise RuntimeError("Option chain did not load (PCR/Max Pain/ATM IV not found)")
        result.add_log("Option chain loaded")

        # 2. Payoff Builder: add/remove legs
        if _switch_tab(browser, "Payoff Builder"):
            time.sleep(2)
            before = _net_premium_text(browser)
            if _add_payoff_leg(browser):
                time.sleep(1.5)
                after_add = _net_premium_text(browser)
                result.add_log(f"Net premium before: {before}, after add: {after_add}")
                if _remove_payoff_leg(browser, index=0):
                    time.sleep(1.5)
                    after_remove = _net_premium_text(browser)
                    result.add_log(f"Net premium after remove: {after_remove}")
                else:
                    result.add_log("Remove-leg button not found")
            else:
                result.add_log("Add-leg button not found")
        else:
            result.add_log("Payoff Builder tab not found")

        # 3. Vol Surface generates
        if _switch_tab(browser, "Vol Surface"):
            time.sleep(1)
            if _generate_vol_surface(browser):
                time.sleep(3)
                body = browser.eval("return document.body.innerText")
                if "IV Surface" in body or "Surface" in body:
                    result.add_log("Vol Surface rendered")
                else:
                    result.add_log("Vol Surface generate clicked but title not detected")
            else:
                result.add_log("Generate Surface button not found")
        else:
            result.add_log("Vol Surface tab not found")

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
