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
            if self.profile:
                args.extend(["--profile", self.profile])
            # Only pass headed/headless when browser-use is launching the
            # browser. With CDP these flags cause session-config mismatches.
            args.append("--headed" if self.headed else "--headless")
        args.extend(["--session", self.session])
        return args

    def _reuse_args(self) -> list[str]:
        """Args for subsequent commands in an already-running session."""
        # Do NOT pass --cdp-url here: browser-use stores it in the session state
        # and re-supplying it makes the CLI think the config has changed.
        args = [self._bin]
        # Mirror _start_args: only pass headed/headless for non-CDP sessions.
        if not self.cdp_url:
            args.append("--headed" if self.headed else "--headless")
        args.extend(["--session", self.session])
        return args

    def _session_state_path(self) -> Path:
        return Path.home() / ".browser-use" / f"{self.session}.state.json"

    def _session_is_running(self) -> bool:
        """Check whether a browser-use session is already alive on disk."""
        path = self._session_state_path()
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            return data.get("phase") == "running"
        except Exception:
            return False

    def _run(self, sub_args: list[str], timeout: int = 60) -> str:
        # Use start args only when we are really creating the session. If the
        # session is already running (e.g. leftover from a previous run or a
        # freshly-created Browser object), re-use it so browser-use does not
        # complain about a "different config".
        if not self._started and not self._session_is_running():
            cmd = self._start_args() + sub_args
        else:
            cmd = self._reuse_args() + sub_args
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
            self._run(["open", url], timeout=timeout)
            return
        # For CDP reuse, navigate the current tab via JS instead of using
        # browser-use `open`, which tends to create a new (logged-out) tab.
        self._attach_to_active_tab()
        self.eval(f"window.location.href = {json.dumps(url)};")
        # Wait for navigation to settle.
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = self.current_url()
            if isinstance(current, str) and url in current:
                return
            time.sleep(0.5)

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

    def _attach_to_active_tab(self) -> None:
        """When reusing a physical Chrome via CDP, browser-use sometimes attaches
        to a fresh `about:blank` target instead of the existing tab. Switch to
        the active tab (index 0) on the first command so subsequent operations
        run in the real page.
        """
        if self.cdp_url and self.reuse and not self._started:
            try:
                self._run(["tab", "switch", "0"], timeout=30)
            except BrowserError:
                pass

    def current_url(self) -> str:
        self._attach_to_active_tab()
        return self.eval("window.location.href")

    def tab_list(self) -> list[tuple[int, str]]:
        """Return a list of (index, url) for every open tab."""
        out = self._run(["tab", "list"], timeout=30)
        tabs: list[tuple[int, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("TAB"):
                continue
            parts = line.split(None, 1)
            if parts:
                try:
                    idx = int(parts[0])
                    url = parts[1] if len(parts) > 1 else ""
                    tabs.append((idx, url))
                except ValueError:
                    continue
        return tabs

    def tab_new(self) -> int:
        """Open a new blank tab and return its index."""
        before = {idx for idx, _ in self.tab_list()}
        self._run(["tab", "new"], timeout=30)
        after = self.tab_list()
        for idx, _ in after:
            if idx not in before:
                return idx
        # Fallback: newest tab is the last one in the list.
        return after[-1][0] if after else 0

    def tab_switch(self, index: int) -> None:
        """Switch to the tab at the given index."""
        self._run(["tab", "switch", str(index)], timeout=30)
