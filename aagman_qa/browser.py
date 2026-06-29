import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class BrowserError(Exception):
    pass


class Browser:
    def __init__(
        self,
        session: str,
        headed: bool = True,
        profile: str | None = None,
        cdp_url: str | None = None,
        reuse: bool = False,
    ):
        self.session = session
        self.headed = headed
        self.profile = profile
        self.cdp_url = cdp_url
        self.reuse = reuse
        self._started = False
        self._bin = shutil.which("browser-use")
        if not self._bin:
            raise BrowserError("browser-use CLI not found on PATH")

    def _start_args(self) -> list[str]:
        """Args used for the first command that creates the session."""
        args = [self._bin]
        if self.cdp_url:
            args.extend(["--cdp-url", self.cdp_url])
        else:
            if self.headed:
                args.append("--headed")
            if self.profile:
                args.extend(["--profile", self.profile])
        args.extend(["--session", self.session])
        return args

    def _reuse_args(self) -> list[str]:
        """Args for subsequent commands in an already-running session."""
        return [self._bin, "--session", self.session]

    def _run(self, sub_args: list[str], timeout: int = 60) -> str:
        if self.reuse or self._started:
            cmd = self._reuse_args() + sub_args
        else:
            cmd = self._start_args() + sub_args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise BrowserError(f"browser-use failed: {' '.join(sub_args)}\n{err}")
        self._started = True
        return result.stdout.strip()

    def open(self, url: str, timeout: int = 60) -> None:
        if not self.reuse:
            # Ensure a fresh session for this run.
            self._force_close()
        if self.cdp_url and not self._started:
            # browser-use requires `connect` first for CDP sessions.
            self._run(["connect"], timeout=timeout)
        self._run(["open", url], timeout=timeout)

    def _force_close(self) -> None:
        try:
            subprocess.run(
                [self._bin, "--session", self.session, "close"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass
        self._started = False

    def close(self) -> None:
        self._force_close()

    def state(self) -> str:
        return self._run(["state"], timeout=30)

    def eval(self, script: str, timeout: int = 30) -> Any:
        out = self._run(["eval", script], timeout=timeout)
        if out.startswith("result: "):
            out = out[len("result: "):]
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def screenshot(self, path: Path, full_page: bool = False) -> Path:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        args = ["screenshot", str(path)]
        if full_page:
            args.append("--full")
        self._run(args, timeout=60)
        return path

    def click(self, index: int) -> None:
        self._run(["click", str(index)], timeout=30)

    def input(self, index: int, text: str) -> None:
        self._run(["input", str(index), text], timeout=30)

    def click_by_text(self, tag: str, text: str, partial: bool = True) -> bool:
        script = f"""
(() => {{
  const els = Array.from(document.querySelectorAll('{tag}'));
  const idx = els.findIndex(e => {'e.textContent.includes(' if partial else 'e.textContent.trim() === '}"{text}"{')'});
  if (idx >= 0) {{ els[idx].click(); return true; }}
  return false;
}})()
"""
        return bool(self.eval(script))

    def wait_for_selector(
        self, selector: str, timeout: int = 30, interval: float = 0.5
    ) -> bool:
        script = f"!!document.querySelector({json.dumps(selector)})"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.eval(script):
                return True
            time.sleep(interval)
        return False

    def wait_for_text(
        self, text: str, timeout: int = 30, interval: float = 0.5
    ) -> bool:
        script = f"document.body.innerText.includes({json.dumps(text)})"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.eval(script):
                return True
            time.sleep(interval)
        return False

    def has_text(self, text: str) -> bool:
        script = f"document.body.innerText.includes({json.dumps(text)})"
        return bool(self.eval(script))

    def has_any_text(self, texts: list[str]) -> bool:
        script = (
            "const t = document.body.innerText; ["
            + ",".join(json.dumps(t) for t in texts)
            + "].some(x => t.includes(x))"
        )
        return bool(self.eval(script))

    def find_state_index(self, pattern: str) -> int | None:
        state = self.state()
        for line in state.splitlines():
            if pattern.lower() in line.lower():
                m = re.search(r"\[(\d+)\]", line)
                if m:
                    return int(m.group(1))
        return None

    def current_url(self) -> str:
        return self.eval("window.location.href")
