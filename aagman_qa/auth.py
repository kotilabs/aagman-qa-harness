import re
import sys
import time

from .browser import Browser
from . import config


def _interactive() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()


def _find_index(state: str, *patterns: str) -> int | None:
    for line in state.splitlines():
        for pat in patterns:
            if pat.lower() in line.lower():
                m = re.search(r"\[(\d+)\]", line)
                if m:
                    return int(m.group(1))
    return None


def _fill_phone(browser: Browser, phone: str) -> None:
    state = browser.state()
    idx = _find_index(state, "id=phone", "type=tel", "placeholder=Enter your mobile")
    if idx is None:
        raise RuntimeError("Phone input not found in accessibility tree")
    browser.input(idx, phone)


def _click_continue(browser: Browser) -> None:
    state = browser.state()
    idx = _find_index(state, ">Continue<", "type=submit")
    if idx is None:
        raise RuntimeError("Continue button not found")
    browser.click(idx)


def _switch_to_otp_if_needed(browser: Browser) -> None:
    state = browser.state()
    # Only switch if we are still on the passkey screen and an SMS option exists.
    idx = _find_index(state, "login-switch-to-otp", "Use SMS code instead", "Send code via SMS")
    if idx is not None:
        browser.click(idx)
        time.sleep(2)


def _fill_otp(browser: Browser, otp: str) -> None:
    script = f"""
(() => {{
  const allInputs = [];
  const walk = (root) => {{
    root.querySelectorAll('input').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => {{
      if (el.shadowRoot) walk(el.shadowRoot);
    }});
  }};
  walk(document);
  const boxes = allInputs.filter(i => i.autocomplete === 'one-time-code' || i.inputMode === 'numeric');
  if (boxes.length < {len(otp)}) return 'boxes:' + boxes.length;
  '{otp}'.split('').forEach((d, i) => {{
    boxes[i].value = d;
    boxes[i].dispatchEvent(new Event('input', {{ bubbles: true }}));
    boxes[i].dispatchEvent(new Event('change', {{ bubbles: true }}));
  }});
  return 'OK';
}})()
"""
    res = browser.eval(script)
    if not str(res).startswith("OK"):
        raise RuntimeError(f"Failed to fill OTP boxes: {res}")


def _click_verify(browser: Browser) -> None:
    state = browser.state()
    idx = _find_index(state, ">Verify<", "aria-label=Verify", "type=submit")
    if idx is not None:
        browser.click(idx)


def _is_logged_in(browser: Browser) -> bool:
    url = browser.current_url()
    return isinstance(url, str) and "/login" not in url and url.startswith("http")


class LoginRequiredError(Exception):
    pass


def login(
    browser: Browser,
    base_url: str,
    phone: str | None = None,
    otp: str | None = None,
) -> None:
    browser.open(base_url)
    time.sleep(2)

    if _is_logged_in(browser):
        return

    phone = phone or config.phone()
    otp = otp or config.otp()

    if not phone or not otp:
        raise LoginRequiredError(
            "Aagman login screen detected. "
            "Log in manually and rerun, or provide --phone and --otp / set AAGMAN_PHONE and AAGMAN_OTP."
        )

    _fill_phone(browser, phone)
    time.sleep(0.5)
    _click_continue(browser)
    time.sleep(3)

    _switch_to_otp_if_needed(browser)

    _fill_otp(browser, otp)
    time.sleep(0.5)
    _click_verify(browser)

    for _ in range(30):
        if _is_logged_in(browser):
            return
        time.sleep(1)
    raise RuntimeError("Login did not redirect to dashboard")


def login_with_profile(browser: Browser, base_url: str) -> None:
    browser.open(base_url)
    for _ in range(15):
        if _is_logged_in(browser):
            return
        time.sleep(1)
    raise RuntimeError("Profile login did not succeed")
