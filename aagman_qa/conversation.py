"""Read and scroll Aagman chat transcripts from the browser page."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .browser import Browser


@dataclass(frozen=True)
class Message:
    role: str | None  # 'user', 'assistant', or None when unknown
    text: str

    def normalized(self) -> str:
        return " ".join(self.text.split()).lower().strip("\"' ")


# Common selectors / heuristics for locating chat messages.
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

_MESSAGE_SELECTOR_STRATEGIES = [
    # OpenAI / ChatGPT-like markup.
    """
    const msgs = [];
    document.querySelectorAll('[data-message-author-role]').forEach(el => {
      const role = el.getAttribute('data-message-author-role');
      const text = (el.innerText || '').trim();
      if (text && (role === 'user' || role === 'assistant')) msgs.push({role, text});
    });
    return msgs;
    """,
    # Generic data-testid message wrappers.
    """
    const msgs = [];
    document.querySelectorAll('[data-testid="chat-message"], [data-testid="conversation-turn"], [data-testid="message"]').forEach(el => {
      const text = (el.innerText || '').trim();
      if (!text) return;
      const cls = (el.className || '').toLowerCase();
      let role = null;
      if (/\\buser\\b/.test(cls)) role = 'user';
      else if (/\\b(assistant|bot|agent)\\b/.test(cls)) role = 'assistant';
      msgs.push({role, text});
    });
    return msgs;
    """,
    # Explicit role attributes.
    """
    const msgs = [];
    document.querySelectorAll('[data-role="user"], [data-role="assistant"], [data-role="bot"]').forEach(el => {
      const text = (el.innerText || '').trim();
      if (!text) return;
      const role = el.getAttribute('data-role');
      msgs.push({role: role === 'bot' ? 'assistant' : role, text});
    });
    return msgs;
    """,
]


def scroll_chat_to_bottom(browser: Browser) -> None:
    """Scroll the chat/message container to the bottom so the latest reply is in the DOM / view."""
    script = f"""
(() => {{
  const findContainer = () => {{
    const selectors = {json.dumps(_CHAT_CONTAINER_SELECTORS)};
    for (const s of selectors) {{
      const el = document.querySelector(s);
      if (el) return el;
    }}
    const all = Array.from(document.querySelectorAll('*'));
    return all.filter(e => e.scrollHeight > e.clientHeight + 10)
              .sort((a, b) => b.scrollHeight - a.scrollHeight)[0] || document.documentElement;
  }};
  const el = findContainer();
  el.scrollTo({{ top: el.scrollHeight, behavior: 'instant' }});
  el.scrollTop = el.scrollHeight;
  return {{tagName: el.tagName, scrollTop: el.scrollTop, scrollHeight: el.scrollHeight}};
}})()
"""
    browser.eval(script)


def extract_messages(browser: Browser) -> list[Message]:
    """Extract the visible chat transcript from the page.

    Returns a list of :class:`Message` objects. The role is best-effort; when
    the page doesn't expose explicit role markers, ``role`` may be ``None`` and
    callers should disambiguate with the prompts they know they sent.
    """
    for strategy in _MESSAGE_SELECTOR_STRATEGIES:
        script = f"""
(() => {{
  try {{
    const result = (() => {{ {strategy} }})();
    if (Array.isArray(result) && result.length >= 1) return result;
  }} catch (e) {{}}
  return [];
}})()
"""
        try:
            raw = browser.eval(script)
            if not isinstance(raw, list):
                continue
            messages = [Message(role=m.get("role"), text=m.get("text", "").strip()) for m in raw]
            messages = [m for m in messages if m.text]
            if messages:
                return messages
        except Exception:
            continue

    # Last resort: take text blocks from the chat container children.
    script = f"""
(() => {{
  const selectors = {json.dumps(_CHAT_CONTAINER_SELECTORS)};
  let container = null;
  for (const s of selectors) {{
    container = document.querySelector(s);
    if (container) break;
  }}
  if (!container) container = document.body;
  const blocks = [];
  const walk = (node, depth = 0) => {{
    if (depth > 30) return;
    if (node.childNodes.length === 0) {{
      const text = (node.textContent || '').trim();
      if (text.length > 5) blocks.push(text);
      return;
    }}
    // Prefer leaf-ish containers.
    if (node.children.length === 0 || node.tagName === 'P' || (node.tagName === 'DIV' && node.children.length <= 2)) {{
      const text = (node.innerText || '').trim();
      if (text.length > 5 && text.length < 5000) blocks.push(text);
    }}
    Array.from(node.children).forEach(c => walk(c, depth + 1));
  }};
  walk(container);
  // De-duplicate nested blocks (keep longest, prefer non-nested).
  const uniq = [];
  for (const b of blocks) {{
    if (!uniq.some(u => u.includes(b) && u !== b)) uniq.push(b);
  }}
  return uniq.slice(-20).map(text => ({{role: null, text}}));
}})()
"""
    raw = browser.eval(script)
    if isinstance(raw, list):
        return [Message(role=m.get("role"), text=m.get("text", "").strip()) for m in raw if m.get("text", "").strip()]
    return []


def classify_unknown_roles(messages: list[Message], known_user_texts: Iterable[str]) -> list[Message]:
    """Assign roles to messages whose role is unknown by matching against sent user prompts."""
    normalized_user = {" ".join(t.split()).lower().strip("\"' ") for t in known_user_texts if t}
    classified = []
    for msg in messages:
        if msg.role in ("user", "assistant"):
            classified.append(msg)
            continue
        if msg.normalized() in normalized_user:
            classified.append(Message(role="user", text=msg.text))
        else:
            classified.append(Message(role="assistant", text=msg.text))
    return classified


def latest_assistant_message(messages: list[Message], known_user_texts: Iterable[str] | None = None) -> str | None:
    """Return the most recent assistant message text, ignoring known user prompts."""
    if known_user_texts is not None:
        messages = classify_unknown_roles(messages, known_user_texts)
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.text
    return None
