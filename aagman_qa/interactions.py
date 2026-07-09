import re
import time

from .browser import Browser


def submit_aagman_prompt(browser: Browser, prompt: str) -> None:
    """Submit a prompt in Aagman's chat input (works for Backtest, Research, etc.)."""
    # Try accessibility-tree index first.
    input_idx = None
    state = browser.state()
    for line in state.splitlines():
        if "textarea" in line and ("Brief your agents" in line or "placeholder=" in line):
            m = re.search(r"\[(\d+)\]", line)
            if m:
                input_idx = int(m.group(1))
                break

    if input_idx is not None:
        browser.input(input_idx, prompt)
    else:
        # Fallback: shadow-piercing JS fill.
        script = f"""
(() => {{
  const allInputs = [];
  const walk = (root) => {{
    root.querySelectorAll('textarea').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => {{ if (el.shadowRoot) walk(el.shadowRoot); }});
  }};
  walk(document);
  const inp = allInputs.find(i => (i.placeholder || '').includes('Brief your agents')) || allInputs[0];
  if (!inp) return 'NO_INPUT';
  inp.value = {prompt!r};
  inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
  inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return 'OK';
}})()
"""
        res = browser.eval(script)
        if str(res) != "OK":
            raise RuntimeError("Could not find Aagman chat input textarea")

    time.sleep(0.5)

    # Find and click send button.
    state = browser.state()
    send_idx = None
    for line in state.splitlines():
        if "Send message" in line or "title=Send" in line:
            m = re.search(r"\[(\d+)\]", line)
            if m:
                send_idx = int(m.group(1))
                break

    if send_idx is not None:
        browser.click(send_idx)
    else:
        script = f"""
(() => {{
  const allBtns = [];
  const walk = (root) => {{
    root.querySelectorAll('button').forEach(el => allBtns.push(el));
    root.querySelectorAll('*').forEach(el => {{ if (el.shadowRoot) walk(el.shadowRoot); }});
  }};
  walk(document);
  const btn = allBtns.find(b => /send/i.test(b.textContent.trim()) || /send/i.test(b.getAttribute('title') || ''));
  if (btn) {{ btn.click(); return 'OK'; }}
  return 'NO_SEND';
}})()
"""
        if str(browser.eval(script)) != "OK":
            # Last resort: press Enter on the textarea.
            browser.eval(f"""
(() => {{
  const allInputs = [];
  const walk = (root) => {{
    root.querySelectorAll('textarea').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => {{ if (el.shadowRoot) walk(el.shadowRoot); }});
  }};
  walk(document);
  const inp = allInputs.find(i => (i.placeholder || '').includes('Brief your agents')) || allInputs[0];
  if (inp) inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));
}})()
""")

    # Wait for the message to actually leave the input box. The send button can be
    # disabled while Aagman is "Thinking", so retry if the text is still present.
    for _ in range(10):
        time.sleep(0.5)
        remaining = browser.eval("""
(() => {
  const allInputs = [];
  const walk = (root) => {
    root.querySelectorAll('textarea').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  walk(document);
  const inp = allInputs.find(i => (i.placeholder || '').includes('Brief your agents')) || allInputs[0];
  return inp ? inp.value.trim() : '';
})()
""")
        if not str(remaining):
            break
        # Text still there — try pressing Enter to force send.
        browser.eval("""
(() => {
  const allInputs = [];
  const walk = (root) => {
    root.querySelectorAll('textarea').forEach(el => allInputs.push(el));
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
  };
  walk(document);
  const inp = allInputs.find(i => (i.placeholder || '').includes('Brief your agents')) || allInputs[0];
  if (inp) {
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    return 'RETRY_ENTER';
  }
  return 'NO_INPUT';
})()
""")
    else:
        time.sleep(0.5)
