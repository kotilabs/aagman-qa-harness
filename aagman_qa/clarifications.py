"""Clarification detection and answer providers for the Aagman QA harness.

When Aagman asks a follow-up question instead of returning a result, the harness
can either:
  1. Look up a pre-defined answer from the manifest (`clarifications` map).
  2. Apply rule-based defaults for common under-specified prompts.
  3. Ask an LLM (DeepSeek / Kimi / Moonshot) to answer in context.
  4. Pause and ask the Kimi CLI agent / human to answer in real time.

The interactive provider writes the pending question to a file and polls for an
answer file created by `aagman-qa answer --run-id <id> --test-id <id> --text "..."`.
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

from .browser import Browser


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------

_CHAT_CONTAINER_SELECTORS = [
    "[role='log']",
    "[role='list']",
    "[data-testid='chat-container']",
    ".chat-container",
    ".chat-messages",
    ".messages",
    "[class*='chat'][class*='container']",
    "[class*='messages']",
]


def _scroll_chat_to_bottom(browser: Browser) -> None:
    """Scroll the chat/message container to the bottom so the latest reply is visible."""
    import json as _json
    script = f"""
(() => {{
  const selectors = {_json.dumps(_CHAT_CONTAINER_SELECTORS)};
  let el = null;
  for (const s of selectors) {{
    el = document.querySelector(s);
    if (el) break;
  }}
  if (!el) {{
    const all = Array.from(document.querySelectorAll('*'));
    el = all.filter(e => e.scrollHeight > e.clientHeight + 10)
            .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  }}
  if (!el) el = document.documentElement;
  el.scrollTo({{ top: el.scrollHeight, behavior: 'instant' }});
  el.scrollTop = el.scrollHeight;
  return {{ ok: true }};
}})()
"""
    try:
        browser.eval(script)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_CLARIFICATION_PHRASES = [
    "how should",
    "what should",
    "would you like",
    "please specify",
    "please clarify",
    "i need a bit",
    "i need more",
    "need more",
    "clarification",
    "what date range",
    "which",
    "how much",
    "how many",
    "do you want",
    "should i",
    "can you specify",
]


def _clean_body_text(browser: Browser) -> str:
    """Return document body text with any active user-input text removed.

    This prevents the user's unsent draft answer from hiding the assistant's
    clarification question at the bottom of the chat.
    """
    script = """
(() => {
  let text = document.body.innerText || '';
  const inputs = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"], [contenteditable=""]'));
  for (const el of inputs) {
    const t = el.value || el.innerText || '';
    if (t && text.includes(t)) {
      text = text.replace(t, '');
    }
  }
  return text;
})()
"""
    return str(browser.eval(script))


def detect_clarification(
    browser: Browser,
    expected_markers: list[str],
    error_markers: list[str],
    tail_chars: int = 3000,
) -> Optional[str]:
    """Return the detected clarification question(s), or None if it looks like a normal result/error."""
    # Make sure the latest message is in the rendered viewport/DOM.
    _scroll_chat_to_bottom(browser)
    time.sleep(0.3)

    body = _clean_body_text(browser)

    # If we already have a result or error marker, this is not a clarification.
    for marker in expected_markers + error_markers:
        if marker in body:
            return None

    # Focus on the bottom of the page where the latest chat message lives.
    tail = body[-tail_chars:] if len(body) > tail_chars else body
    tail_lower = tail.lower()

    if "?" not in tail:
        return None

    if not any(phrase in tail_lower for phrase in _CLARIFICATION_PHRASES):
        return None

    # Aagman sometimes asks a numbered list of questions. Capture the whole
    # last assistant message block: from the last clarification phrase (or the
    # last line break before the first question) up to the final question mark.
    phrase_positions = [tail_lower.find(phrase) for phrase in _CLARIFICATION_PHRASES if phrase in tail_lower]
    first_phrase_idx = min(p for p in phrase_positions if p >= 0) if phrase_positions else -1

    q_idx = tail.rfind("?")
    if first_phrase_idx >= 0 and first_phrase_idx < q_idx:
        # Walk back to the start of the line/paragraph containing the phrase.
        start = tail.rfind("\n", 0, first_phrase_idx) + 1
        question = tail[start:q_idx + 1].strip()
    else:
        start = tail.rfind("\n", 0, q_idx) + 1
        question = tail[start:q_idx + 1].strip()

    # Clean up common artifacts.
    question = re.sub(r"\s+", " ", question)
    if len(question) < 5:
        return None
    return question


# ---------------------------------------------------------------------------
# Answer providers
# ---------------------------------------------------------------------------

class AnswerProvider(ABC):
    @abstractmethod
    def get_answer(
        self,
        original_prompt: str,
        question: str,
        page_text: str,
        test: dict,
    ) -> Optional[str]:
        """Return the answer text, or None if this provider cannot answer."""
        ...


class ManifestAnswerProvider(AnswerProvider):
    """Lookup from the test's `clarifications` map."""

    def get_answer(self, original_prompt: str, question: str, page_text: str, test: dict) -> Optional[str]:
        clarifications = test.get("clarifications", {})
        question_lower = question.strip().lower()
        for key, answer in clarifications.items():
            key_lower = key.strip().lower()
            if key_lower == question_lower or key_lower in question_lower:
                return answer
        return None


class DefaultAnswerProvider(AnswerProvider):
    """Rule-based defaults for common under-specified strategy prompts."""

    def get_answer(self, original_prompt: str, question: str, page_text: str, test: dict) -> Optional[str]:
        prompt_lower = original_prompt.lower()
        question_lower = question.lower()

        # Opening-range breakout defaults (catches both the initial under-specified
        # prompt and follow-up questions about sizing/risk).
        if ("opening range" in prompt_lower or "opening-range" in prompt_lower) and "breakout" in prompt_lower:
            return (
                "Opening range: first 30 minutes (09:15–09:45 IST). "
                "Entry: go LONG on a breakout above the 30-min range high, "
                "SHORT on a breakdown below the 30-min range low. "
                "Exit: close the position at 15:15 IST end-of-day. "
                "Stop loss: 1% from entry price. Take profit: 2% from entry price. "
                "Position sizing: 100 shares per trade, starting capital Rs 10,00,000."
            )

        # Sizing + risk question for any strategy.
        if ("size" in question_lower or "position" in question_lower) and "risk" in question_lower:
            return (
                "Use a fixed position size of 100 shares per trade with Rs 10,00,000 starting capital. "
                "Stop loss: 1% from entry price. Take profit: 2% from entry price."
            )

        # Date-range questions
        if any(phrase in question_lower for phrase in ["date range", "time period", "which dates", "what dates"]):
            # Try to pull explicit dates from the prompt.
            dates = re.findall(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", original_prompt)
            if len(dates) >= 2:
                return f"Use the date range already specified: {dates[0]} to {dates[1]}."
            # Default to a sensible 1-year window anchored at 2025.
            return "Use 1 January 2025 to 31 December 2025."

        # Direction ambiguity
        if any(phrase in question_lower for phrase in ["long or short", "direction", "buy or sell"]):
            if "short" in prompt_lower and "long" not in prompt_lower:
                return "Go short only."
            return "Go long only."

        # Timeframe ambiguity
        if any(phrase in question_lower for phrase in ["timeframe", "time frame", "which interval"]):
            tf = re.search(r"(\d+\s*[mhdwM])", original_prompt)
            if tf:
                return f"Use the {tf.group(1)} timeframe as stated in the request."
            return "Use a 1-day timeframe."

        # Confirmation-type questions
        if any(phrase in question_lower for phrase in ["proceed", "run this", "continue", "shall i"]):
            return "Yes, proceed with the parameters I provided."

        # Sizing / capital
        if any(phrase in question_lower for phrase in ["capital", "shares", "position size", "how many", "how much"]):
            if "share" in prompt_lower:
                return "Use the number of shares already specified in the request."
            return "Use a fixed position size of 100 shares per trade with Rs 10,00,000 starting capital."

        # Stop loss / take profit
        if any(phrase in question_lower for phrase in ["stop loss", "take profit", "sl", "tp"]):
            return "Use a 2% stop loss and a 4% take profit unless already specified."

        return None


class LLMAnswerProvider(AnswerProvider):
    """Use an external LLM to answer arbitrary clarification questions in context."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ):
        provider = os.getenv("AAGMAN_QA_LLM_PROVIDER", "auto").lower()
        base_url_env = (os.getenv("AAGMAN_QA_LLM_BASE_URL") or "").rstrip("/")
        model_env = os.getenv("AAGMAN_QA_LLM_MODEL")

        # DeepSeek takes precedence when explicitly configured or when the base URL/model says so.
        is_deepseek = (
            provider == "deepseek"
            or "deepseek" in base_url_env.lower()
            or (model_env and "deepseek" in model_env.lower())
        )

        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or os.getenv("AAGMAN_QA_LLM_API_KEY")

        if is_deepseek:
            self.base_url = base_url_env or "https://api.deepseek.com/v1"
            self.model = model_env or "deepseek-chat"
            self.user_agent = "aagman-qa-harness/0.1.0"
        elif self.api_key and self.api_key.startswith("sk-kimi-"):
            # Kimi Code keys (sk-kimi-...) only work on the Kimi Code endpoint.
            self.base_url = (base_url_env or "https://api.kimi.com/coding/v1").rstrip("/")
            self.model = model_env or "kimi-for-coding"
            self.user_agent = "claude-code/0.1.0"
        else:
            self.base_url = (base_url_env or "https://api.moonshot.ai/v1").rstrip("/")
            self.model = model_env or "kimi-latest"
            self.user_agent = "aagman-qa-harness/0.1.0"

        if base_url:
            self.base_url = base_url.rstrip("/")
        if model:
            self.model = model

        self.timeout = timeout

    def _call_chat_completion(self, messages: list[dict[str, str]]) -> Optional[str]:
        if not self.api_key:
            print("⚠️ LLM answer provider requested but no API key found.", flush=True)
            return None

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 512,
        }
        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )

        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=self.timeout, context=ctx) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            print(f"⚠️ LLM API HTTP error {exc.code}: {body[:300]}", flush=True)
            return None
        except Exception as exc:
            print(f"⚠️ LLM API call failed: {exc}", flush=True)
            return None

    def get_answer(
        self,
        original_prompt: str,
        question: str,
        page_text: str,
        test: dict,
    ) -> Optional[str]:
        system_prompt = (
            "You are a concise trading-strategy assistant for the Indian market. "
            "A user sent a request to Aagman, an Indian trading app. "
            "Aagman replied with a follow-up question. "
            "Answer it directly with concrete parameters. "
            "Do not ask another question. "
            "If any parameters are missing, choose sensible defaults for Indian markets: "
            "market hours 09:15–15:15 IST, starting capital Rs 10,00,000, 100 shares per trade, "
            "stop loss 1%, take profit 2%. "
            "Keep your answer to one short paragraph."
        )

        user_prompt = (
            f"Original request:\n{original_prompt}\n\n"
            f"Aagman's follow-up question:\n{question}\n\n"
            f"Recent page text (for context):\n{page_text[-1500:]}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        answer = self._call_chat_completion(messages)
        if answer:
            print(f"🧠 LLM answer for `{test['id']}`: {answer[:200]}...", flush=True)
        return answer


class InteractiveAnswerProvider(AnswerProvider):
    """Pause the test, write the pending question to disk, and poll for an answer file."""

    def __init__(self, run_id: str, report_dir: Path, poll_interval: float = 3.0):
        self.run_id = run_id
        self.report_dir = report_dir
        self.pending_file = report_dir / "pending_clarifications.json"
        self.answers_dir = report_dir / "answers"
        self.answers_dir.mkdir(parents=True, exist_ok=True)
        self.poll_interval = poll_interval

    def get_answer(
        self,
        original_prompt: str,
        question: str,
        page_text: str,
        test: dict,
    ) -> Optional[str]:
        test_id = test["id"]

        # Persist pending clarification.
        pending_list: list[dict] = []
        if self.pending_file.exists():
            try:
                pending_list = json.loads(self.pending_file.read_text())
            except Exception:
                pending_list = []
        pending_list = [p for p in pending_list if p.get("test_id") != test_id]
        pending_list.append({
            "test_id": test_id,
            "question": question,
            "prompt": original_prompt,
            "page_text": page_text[:2000],
            "asked_at": datetime.now().isoformat(),
        })
        self.pending_file.write_text(json.dumps(pending_list, indent=2), encoding="utf-8")

        answer_file = self.answers_dir / f"{test_id}.txt"
        if answer_file.exists():
            answer_file.unlink()  # stale answer

        # Surface the question to the Kimi CLI / human operator.
        print(f"\n🤖 AGENT QUESTION for test `{test_id}`:", flush=True)
        print(f"   Original prompt: {original_prompt[:160]}...", flush=True)
        print(f"   App asks: {question}\n", flush=True)
        print(
            f"   Reply with:\n"
            f"   aagman-qa answer --run-id {self.run_id} --test-id {test_id} --text \"<your answer>\"\n",
            flush=True,
        )

        deadline = time.time() + test.get("timeout", 180)
        while time.time() < deadline:
            if answer_file.exists():
                return answer_file.read_text(encoding="utf-8").strip()
            time.sleep(self.poll_interval)

        return None


class CompositeAnswerProvider(AnswerProvider):
    """Try manifest, then defaults, then interactive fallback."""

    def __init__(self, providers: list[AnswerProvider]):
        self.providers = providers

    def get_answer(
        self,
        original_prompt: str,
        question: str,
        page_text: str,
        test: dict,
    ) -> Optional[str]:
        for provider in self.providers:
            answer = provider.get_answer(original_prompt, question, page_text, test)
            if answer:
                return answer
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_answer_provider(
    provider_name: str,
    run_id: str,
    report_dir: Path,
) -> AnswerProvider:
    """Return the configured answer provider for a run.

    `provider_name` can be:
      - `manifest`: only manifest lookups
      - `default`:  manifest + rule-based defaults
      - `llm`:      manifest + defaults + LLM answer fallback
      - `interactive`: manifest + defaults + interactive pause
      - `blocked`:    never answer (forces BLOCKED status)
    """
    providers: list[AnswerProvider] = [ManifestAnswerProvider()]

    if provider_name in ("default", "interactive", "llm"):
        providers.append(DefaultAnswerProvider())

    if provider_name == "llm":
        providers.append(LLMAnswerProvider())

    if provider_name == "interactive":
        providers.append(InteractiveAnswerProvider(run_id, report_dir))

    return CompositeAnswerProvider(providers)
