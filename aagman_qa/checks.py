import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .browser import Browser


@dataclass
class TestResult:
    id: str
    status: str  # PASS | FAIL | BLOCKED | ERROR
    duration_sec: float
    message: str = ""
    prompt: str = ""
    title: str = ""
    description: str = ""
    screenshots: list[Path] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def add_log(self, msg: str) -> None:
        self.logs.append(msg)

    def add_screenshot(self, path: Path) -> None:
        self.screenshots.append(path)


class CheckError(Exception):
    pass


def wait_for_any_text(
    browser: Browser,
    texts: list[str],
    timeout: int = 30,
    interval: float = 0.5,
) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = browser.eval("document.body.innerText")
        for t in texts:
            if t in body:
                return t
        time.sleep(interval)
    return None


def assert_no_error_texts(
    browser: Browser,
    error_texts: list[str] | None = None,
) -> None:
    error_texts = error_texts or [
        "Cannot run backtest",
        "Backtest failed",
        "Something went wrong",
        "error occurred",
        "failed to load",
    ]
    body = browser.eval("document.body.innerText")
    for err in error_texts:
        if err.lower() in body.lower():
            raise CheckError(f"Error text detected: {err}")


def assert_texts_present(browser: Browser, texts: list[str]) -> None:
    body = browser.eval("document.body.innerText")
    missing = [t for t in texts if t not in body]
    if missing:
        raise CheckError(f"Missing expected texts: {missing}")


def capture_failure_screenshot(browser: Browser, path: Path, full_page: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scroll to the bottom of the main chat/workspace pane so the failure
    # message (last item) is in view.
    try:
        browser.eval("""
(() => {
  const all = Array.from(document.querySelectorAll('*'));
  const el = all.filter(e => e.scrollHeight > e.clientHeight)
                 .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  if (el) el.scrollTop = el.scrollHeight;
  return 'OK';
})()
""")
    except Exception:
        pass
    browser.screenshot(path, full_page=full_page)
    return path


def with_screenshot(func: Callable, browser: Browser, screenshot_dir: Path, name: str):
    try:
        return func()
    except Exception as exc:
        path = screenshot_dir / f"{name}.png"
        capture_failure_screenshot(browser, path)
        raise CheckError(f"{exc} (screenshot: {path})") from exc
