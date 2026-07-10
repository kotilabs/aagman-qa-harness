# aagman-qa

A CLI QA harness for the [Aagman](https://app.aagman.ai/) web app. It drives a real browser with [`browser-use`](https://github.com/browser-use/browser-use), runs declarative test manifests, screenshots failures, writes local reports, and publishes consolidated results to GitHub — either as issue comments or as new issues.

---

## What it does

| Capability | Status | Description |
|---|---|---|
| **Backtest regression** | ✅ Stable (batch mode) | Submits prompts one-per-workspace, orchestrates `run risk checks` → `run backtest`, and verifies the report card. |
| **Research / Screener smoke** | ✅ Stable (batch mode) | Submits screener prompts in parallel workspaces, lets Aagman process offline, then checks each workspace for a results table or "Found 0 matches". |
| **Charts UI smoke** | ⚠️ Partial | Opens Charts, cycles timeframes, adds indicators, and verifies the canvas. Works in simple cases but selectors are still brittle. |
| **Chart query (natural language)** | ✅ Runner stable; product rendering flaky | Asks for a chart in Research chat, waits for an acknowledgement, screenshots the reply and uses a vision check to confirm a chart rendered. |
| **Options smoke** | 🚧 Experimental | Loads Option Chain, adds/removes Payoff Builder legs, and checks Vol Surface. Selectors are not final. |
| **Failure artifacts** | ✅ | Every failing test captures a full-page screenshot; final report + screenshots are uploaded to a public GitHub artifacts repo so images can be embedded in issues. |
| **GitHub publishing** | ✅ | One command can comment on an existing issue **or** create a new issue with a standardized report template. |

---

## Architecture

```text
                    ┌─────────────────────┐
                    │   Manifest YAML     │
                    │  (tests + prompts)  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
        ┌───────────│    aagman-qa CLI    │────────────┐
        │           │  (argparse)         │            │
        │           └──────────┬──────────┘            │
        │                      │                        │
   ┌────▼─────┐        ┌───────▼────────┐        ┌─────▼──────┐
   │ Browser  │        │   Runners      │        │  Reporter  │
   │ wrapper  │◄──────►│ (backtest/     │◄──────►│ (markdown  │
   │(browser- │        │ research/      │        │  + json)   │
   │  use)    │        │ charts/        │        └─────┬──────┘
   └────┬─────┘        │ options/       │              │
        │              │ chart_query)   │              │
        │              └────────────────┘              │
        │                                              │
        ▼                                              ▼
   Chrome/Edge                                  GitHub Publisher
   (CDP / profile)                      (issue comments OR new issues)
                                              │
                                              ▼
                              ┌───────────────────────────────┐
                              │  Screenshots repo             │
                              │  iamaryansinha/               │
                              │     aagman-qa-screenshots     │
                              └───────────────────────────────┘
```

### Module responsibilities

| File | Responsibility |
|---|---|
| `aagman_qa/cli.py` | Commands: `run`, `push`, `answer`. Flags for batch modes, batch sizes, delays, and timeouts. |
| `aagman_qa/config.py` | Environment variables, manifest loading, run-id generation, environment URLs. |
| `aagman_qa/browser.py` | Wraps the `browser-use` CLI: start Chrome, attach to CDP, switch tabs, navigate, evaluate JS, screenshot. |
| `aagman_qa/auth.py` | Phone/OTP login flow. Can be skipped when Chrome is already logged in. |
| `aagman_qa/interactions.py` | Generic chat-input helpers used by multiple runners. |
| `aagman_qa/checks.py` | `TestResult` dataclass, screenshot helpers, success/error heuristics. |
| `aagman_qa/reporter.py` | Writes local `report.md` and `results.json`. |
| `aagman_qa/github_publisher.py` | Uploads screenshots to the artifacts repo, pushes the report branch, and posts the GitHub issue/comment. |
| `aagman_qa/runners/backtest.py` | Single + phased batch backtest runner. |
| `aagman_qa/runners/research.py` | Single + batch research/screener runner. |
| `aagman_qa/runners/charts.py` | Charts UI smoke runner. |
| `aagman_qa/runners/chart_query.py` | Natural-language chart request runner with vision verification. |
| `aagman_qa/runners/options.py` | Option chain / payoff builder / vol surface smoke runner. |

---

## Setup

### 1. Clone / enter the project

```bash
cd aagman-qa-harness
cp .env.example .env
```

### 2. Install

This project uses [`uv`](https://docs.astral.sh/uv/):

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

After installation the `aagman-qa` command is available.

### 3. Required environment variables

| Var | Purpose |
|---|---|
| `GITHUB_TOKEN` or `GH_TOKEN` | Token for creating issues / posting comments. Needs `repo` scope. |
| `GH_TOKEN_PERSONAL` (or `SCREENSHOTS_TOKEN`) | Token with write access to the public screenshots repo. Used for uploading artifacts. Defaults to `GITHUB_TOKEN` if unset. |

### 4. Optional environment variables

| Var | Purpose |
|---|---|
| `AAGMAN_PHONE` | Your registered phone number. Used for automated OTP login. |
| `AAGMAN_OTP` | Pre-seed OTP for non-interactive runs. If both `AAGMAN_PHONE` and `AAGMAN_OTP` are set, the harness logs in automatically. |
| `AAGMAN_QA_SCREENSHOTS_REPO` | `<owner>/<repo>` for artifact uploads. Default: `iamaryansinha/aagman-qa-screenshots`. |
| `GITHUB_OWNER` / `GITHUB_REPO` | Primary repo for issue creation. Default: `kotilabs/aagman-v2`. |
| `BROWSER_USE_CDP_URL` | Attach to an existing Chrome debug instance, e.g. `http://localhost:9222`. |
| `BROWSER_USE_PROFILE` | Use a saved `browser-use` profile so you rarely have to log in. |
| `TESTER_NAME` | Name shown in generated issue reports. |

### 5. Launch Chrome for physical-browser runs

This harness is designed to run against the user’s real Chrome window (not headless):

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="${PWD}/.chrome-data" \
  https://app.staging.v2.aagman.ai/
```

### 6. Log in

If you have already logged in inside the Chrome window, just pass `--reuse-session` and the harness will use that session:

```bash
aagman-qa run --env staging \
  --manifest manifests/backtest-mixed-8.yaml \
  --cdp-url http://localhost:9222 \
  --reuse-session \
  --batch-backtest
```

If you are not logged in, run without `--reuse-session`. The harness will open the Aagman staging page in the browser and wait for you:

```bash
aagman-qa run --env staging \
  --manifest manifests/backtest-mixed-8.yaml \
  --cdp-url http://localhost:9222 \
  --batch-backtest
```

You will see a prompt like:

```text
🔐 Aagman login required.
   The browser is open at: https://app.staging.v2.aagman.ai/
   Please log in using the physical Chrome window.
   Once you are logged in, type 'logged in' here and press Enter so I can continue.
```

Log in inside Chrome, then type `logged in` in the terminal and press **Enter**.

To skip the manual step entirely, set `AAGMAN_PHONE` and `AAGMAN_OTP` (or pass `--phone` and `--otp`).

---

## Usage

### Run a manifest

```bash
# Backtests in phased batch mode
aagman-qa run --env staging \
  --manifest manifests/backtest-mixed-8.yaml \
  --cdp-url http://localhost:9222 \
  --reuse-session \
  --batch-backtest

# Research / screener in batch mode
aagman-qa run --env staging \
  --manifest manifests/research-mixed-7.yaml \
  --cdp-url http://localhost:9222 \
  --reuse-session \
  --batch-research

# Mixed manifest: research batched, backtests batched, everything else sequential
aagman-qa run --env staging \
  --manifest manifests/smoke-charts-options.yaml \
  --cdp-url http://localhost:9222 \
  --reuse-session \
  --batch-research \
  --batch-backtest

# Create a new GitHub issue with the full report + failure screenshots
aagman-qa run --env staging \
  --manifest manifests/backtest-mixed-8.yaml \
  --cdp-url http://localhost:9222 \
  --reuse-session \
  --batch-backtest \
  --push --create-issue
```

If the app is logged out, the harness will open the Aagman page and ask you to log in manually (unless `AAGMAN_PHONE` and `AAGMAN_OTP` are provided, in which case it logs in automatically).

### Push an existing run to GitHub

```bash
# Comment on an existing issue
aagman-qa push --run-id <run-id> --issue 2223

# Create a new issue instead
aagman-qa push --run-id <run-id> --create-issue
```

Artifacts are always uploaded first to `AAGMAN_QA_SCREENSHOTS_REPO` so images can be embedded inline.

### Review locally

```bash
cat reports/<run-id>/report.md
# or
jq . reports/<run-id>/results.json
```

---

## Manifest format

A manifest is a YAML file listing one or more tests. Each test gets a unique `id`, a `type`, and type-specific fields.

```yaml
name: prod-backtest-smoke
env: staging
tests:
  - id: bt-01
    type: backtest
    prompt: |
      Run a backtest for a long straddle on NIFTY 50 for the last 30 days.
      Entry: buy ATM call and ATM put at 9:20 AM.
      Exit: 3:15 PM or 10% profit / 5% stop loss.
    expected_contains:
      - "Total PnL"
      - "Max Drawdown"
      - "Win Rate"
    error_markers:
      - "Cannot run backtest"
      - "Backtest failed"
    timeout: 240

  - id: research-01
    type: research
    prompt: "Find Nifty 50 stocks above 200 SMA with RSI between 50 and 70"
    success_markers:
      - "Found"
      - "Symbol"
      - "LTP"
    timeout: 300

  - id: charts-01
    type: charts
    title: "Chart timeframe + indicator smoke"
    description: "Verify chart loads, cycles timeframes, and adds SMA/EMA/RSI."
    timeframes: ["1m", "5m", "15m", "1h", "1d"]
    indicators: ["SMA", "EMA", "RSI"]
    timeout: 120

  - id: options-01
    type: options
    title: "Option chain + payoff + vol surface"
    description: "Load NIFTY option chain, add a straddle to payoff builder, and render vol surface."
    timeout: 120

  - id: chart-q-01
    type: chart_query
    prompt: "Show me a candlestick chart for Reliance Industries"
    symbol: "RELIANCE"
    description: "candlestick chart"
    timeout: 120
```

### Test types

| Type | Fields | Pass criteria |
|---|---|---|
| `backtest` | `prompt`, `expected_contains`, `error_markers`, `timeout` | Report card appears and contains all expected markers. Zero-trades report is OK. |
| `research` / `screener` | `prompt`, `success_markers`, `timeout` | Results table or "Found 0 matches" appears. |
| `charts` | `title`, `description`, `timeframes`, `indicators`, `timeout` | Chart canvas loads; each requested timeframe is clicked; indicators are added successfully. |
| `options` | `title`, `description`, `timeout` | Chain loads (`PCR`, `Max Pain`, `ATM IV`); payoff builder updates; vol surface renders. |
| `chart_query` | `prompt`, `symbol`, `description`, `timeout`, `vision_timeout` | Assistant acknowledges a chart, a screenshot is taken, and a vision check confirms the described chart rendered. |

For charts/options/chart_query, `title` and `description` are used in the GitHub issue sections when there is no natural-language `prompt`.

---

## How each runner works

### Backtest runner (`aagman_qa/runners/backtest.py`)

Backtests have a multi-step conversation flow. Aagman does **not** run immediately after the prompt; it usually asks for `run risk checks` first, then `run backtest`, then finally renders a report card.

#### Single mode (`run()`)

1. Navigate to the backtest home page.
2. Submit the prompt. Aagman creates a new workspace and may ask clarifying questions.
3. Poll the page in a loop looking for:
   - explicit error markers → **FAIL**
   - report-card markers (`Total PnL`, `Max Drawdown`, `Win Rate`) → **PASS**
   - the text `run backtest` → send `run backtest`
   - the text `run risk checks` → send `run risk checks`
   - clarification questions → answer via the configured `answer_provider` (interactive, manifest, LLM, or block)
4. Time out if none of the above happen within `timeout` seconds (default 240).

Matching is case-insensitive. Zero-trade reports count as a pass as long as the markers are present.

#### Batch mode (`run_batch()`)

Batch mode is the recommended way to run backtests. It submits prompts up front, lets Aagman process in the background, and only re-visits workspaces when results are likely ready.

Default parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `batch_size` | 8 | Maximum prompts submitted in one chunk. |
| `risk_delay` | 120 s | Wait after submission before sending `run risk checks`. |
| `backtest_delay` | 180 s | Wait after risk checks before sending `run backtest`. |
| `result_delay` | 240 s | Wait after `run backtest` before checking for the report card. |
| `check_timeout` | 120 s | Per-workspace timeout while reading the final report card. |

Flow for each chunk:

```text
for each test:
    navigate to backtest home
    submit prompt
    capture workspace UUID URL

sleep(risk_delay)
for each workspace:
    visit workspace
    if page says "run risk checks":
        send "run risk checks"

sleep(backtest_delay)
for each workspace:
    visit workspace
    if page says "run backtest":
        send "run backtest"

sleep(result_delay)
for each workspace:
    visit workspace
    scroll / poll for report-card markers
    PASS/FAIL + screenshot
```

You can tune the delays with CLI flags:

```bash
aagman-qa run ... --batch-backtest \
  --batch-size 6 \
  --batch-delay 300 \
  --batch-check-timeout 120
```

(`--batch-delay` controls the first wait; backtest-specific internal delays are currently fixed at 120/180/240 s. Override them in code if you need different values.)

### Research / Screener runner (`aagman_qa/runners/research.py`)

Research prompts return a results table (or "Found 0 matches"). Aagman can take several minutes to finish, so the single-mode timeout often fires before the table renders. Batch mode is the stable path.

#### Single mode (`run()`)

1. Navigate to the Research workspace.
2. Start a new screener chat.
3. Submit the prompt.
4. Wait for a result in the latest assistant message or in the **Results** tab.
5. If Aagman asks a clarification, answer it (up to 3 rounds).
6. Mark **PASS** when a result phrase/table is detected, **FAIL** on error markers or timeout.

Result detection uses the main workspace text (excluding nav/header) and looks for patterns like:

- `Found N matches`
- `N stocks match`
- a rendered table containing `Symbol` + (`LTP` / `RSI` / `Close`)

#### Batch mode (`run_batch()`)

This mirrors the workflow that works best against Aagman’s long-running screeners:

Default parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `batch_size` | 10 | Maximum prompts submitted in one chunk. |
| `settle_delay` | 480 s | Time to let Aagman finish processing before checking results (8 minutes). |
| `check_timeout` | 90 s | Per-workspace timeout while reading the final verdict. |

Flow for each chunk:

```text
navigate to Research workspace
for each test:
    start a new research chat
    submit prompt
    capture workspace UUID URL (/screener/<uuid>)

sleep(settle_delay)

for each workspace:
    navigate back to the saved URL
    scroll to the absolute bottom of the workspace
    look at the last assistant message for a verdict
    PASS/FAIL + screenshot
```

The key insight is that the verdict lives at the **bottom of the chat/workspace**, not in a separate Results tab. The batch runner records the UUID workspace URL immediately after submission so it can return to each workspace reliably after the settle period.

Tune the settle delay:

```bash
aagman-qa run ... --batch-research \
  --batch-size 10 \
  --batch-delay 480 \
  --batch-check-timeout 90
```

### Charts UI runner (`aagman_qa/runners/charts.py`)

1. Navigate to `/charts`.
2. Verify a `<canvas>` element exists.
3. Click each timeframe button in `timeframes` (defaults to `1m, 5m, 15m, 1h, 1d`).
4. Open the indicator menu and add up to 3 indicators from `indicators` (defaults to `SMA, EMA, RSI`).
5. Mark **PASS** if the canvas is present and all requested actions complete.

This runner is currently brittle because Aagman’s chart toolbar selectors vary. It is useful for quick smoke checks but not yet reliable enough for CI.

### Chart query runner (`aagman_qa/runners/chart_query.py`)

For natural-language chart requests such as *"Show me a candlestick chart for Reliance Industries"*.

1. Navigate to Research and start a new chat.
2. Submit the prompt.
3. Wait for Aagman to acknowledge the chart request (`here's your chart`, `chart for`, etc.).
4. Wait a few seconds for the widget to render.
5. Screenshot the latest reply.
6. Run a vision check (`verify_chart`) confirming the described chart is visible.
7. If vision says no chart, wait 10 s, screenshot again, and retry once.
8. Mark **PASS** only if the vision check says the chart rendered.

Requires a vision provider. If no vision provider is configured the runner falls back to text-only detection and may be less reliable.

### Options runner (`aagman_qa/runners/options.py`)

Experimental smoke test for the option chain module:

1. Navigate to the Options workspace.
2. Load the NIFTY option chain.
3. Verify chain metrics (`PCR`, `Max Pain`, `ATM IV`).
4. Add a leg to the Payoff Builder and verify it updates.
5. Render the Vol Surface.
6. Mark **PASS** if all steps complete.

This runner is not stable yet; the app UI for options is still changing.

---

## Browser wrapper details

`aagman_qa/browser.py` wraps the `browser-use` CLI rather than the Python API. This was chosen because Aagman authentication and some shadow-DOM inputs already work reliably through browser-use.

Key behaviors:

- **CDP reuse**: When `--cdp-url http://localhost:9222 --reuse-session` is passed, the harness attaches to the user’s existing Chrome instead of launching a new browser.
- **Session isolation**: Each run uses a unique session name (`aagman-qa-<run-id>`) so stale session configs from previous runs do not conflict.
- **Active-tab attachment**: On CDP reuse, browser-use sometimes attaches to a fresh `about:blank` target. The wrapper calls `tab switch 0` first to make sure commands run in the real page.
- **Navigation on reuse**: Instead of using `browser-use open` (which can open a new logged-out tab), navigation is done via JavaScript (`window.location.href = ...`) in the current tab.
- **State/eval helpers**: `state()` returns the interactive element index list from browser-use; `eval()` runs arbitrary JS and parses JSON results.

---

## How success / failure is decided

| Screen | PASS | FAIL |
|---|---|---|
| **Backtest** | Report card with `Total PnL`, `Max Drawdown`, `Win Rate` appears. Even zero trades count as a completed report. | Explicit error message or timeout without the report card. |
| **Research / Screener** | Results table or `Found 0 matches` appears in the latest assistant message or Results tab. | Error toast, empty response, spinner timeout. |
| **Charts UI** | Canvas loads; each requested timeframe is selected; up to 3 indicators can be added. | Canvas missing, timeframe hangs, indicator not added. |
| **Chart query** | Assistant acknowledges the chart and a vision check confirms it rendered. | No acknowledgement, vision says no chart, or timeout. |
| **Options** | Chain loads (`PCR`, `Max Pain`, `ATM IV`); Payoff Builder updates after adding/removing legs; Vol Surface renders. | Spinner timeout, empty chain, leg change not reflected, surface missing. |

Failure screenshots are scrolled to the bottom of the main scrollable pane before capture so the latest chat / error is visible.

---

## GitHub issue template

Generated issues follow this structure:

```markdown
## QA Run Summary

- **Date:** 2026-06-24
- **Tester:** Arjun
- **Platform:** staging
- **Result:** 17 / 20 passed

## Summary
One-sentence summary of the run.

## Test-by-Test Results
| ID | Type | Result | Notes |
|---|---|---|---|
| bt-01 | backtest | PASS | ... |

## Failed Tests

### bt-02
**Assessment:** ...
**Severity:** high/medium/low
**Screenshot:** <embedded image>
**Reproduction:** ...

## External Verification
- [ ] Verified by another team member
```

If `--issue` is provided, the report is posted as a comment instead.

---

## Repository structure

```text
aagman-qa-harness/
├── aagman_qa/
│   ├── __init__.py
│   ├── cli.py                 # argparse commands
│   ├── config.py              # env vars, manifest loader
│   ├── browser.py             # browser-use wrapper
│   ├── auth.py                # phone + OTP login
│   ├── interactions.py        # shared chat helpers
│   ├── checks.py              # assertions + failure screenshots
│   ├── reporter.py            # local report generation
│   ├── github_publisher.py    # GitHub upload + issue/comment logic
│   └── runners/
│       ├── __init__.py
│       ├── backtest.py
│       ├── research.py
│       ├── charts.py
│       ├── chart_query.py
│       └── options.py
├── manifests/
│   ├── backtest-mixed-8.yaml
│   ├── research-mixed-7.yaml
│   ├── chart-mixed-5.yaml
│   └── smoke-charts-options.yaml
├── reports/                   # generated, gitignored
├── .gitignore
├── .env.example
├── pyproject.toml
└── README.md
```

Generated artifacts (`reports/`, `.chrome-data*`, `.venv/`, `__pycache__/`, `.env`) are excluded from git.

---

## What is verified to work right now

The harness has been used end-to-end on staging. The current stable paths are:

- ✅ **Backtest batch runs** — 8 mixed backtest prompts passed in a single phased batch run.
- ✅ **Research batch runs** — 7 mixed screener/research prompts passed using the submit-then-settle workflow.
- ✅ **GitHub publishing** — reports + screenshots pushed to `iamaryansinha/aagman-qa-screenshots` and issues created on the configured repo.
- ⚠️ **Charts UI smoke** — works on simple pages, but selectors need maintenance as the UI evolves.
- ✅ **Chart query (vision)** — the runner is stable and correctly reports PASS/FAIL. In the latest run it caught real Aagman rendering issues: 2 of 5 prompts rendered a chart, 3 failed because Aagman either returned text without a chart widget or timed out.
- 🚧 **Options smoke** — still experimental; not yet run in a stable batch.

If you are adding new coverage, prefer **batch backtest** and **batch research**; they are the most reliable.

---

## Development notes

- The harness uses **browser-use over CDP** because Aagman authentication and certain shadow-DOM inputs are already known to work reliably through browser-use.
- Each run gets a unique `run-id` and writes to `reports/<run-id>/`.
- Screenshots are captured as absolute paths (browser-use requires this) and later converted to relative references in the local report.
- The screenshots repo is treated as a separate, public artifact store so failure images can be embedded inline in issues even when the primary repo does not grant push access.

---

## Roadmap / known gaps

- [ ] Add a proper **Charts feature runner** that tests chart functionality end-to-end (not just UI smoke) and wire it up in the CLI/manifests.
- [ ] Stabilize and complete the **Options runner** — option chain, payoff builder, and vol surface smoke tests.

---

## License

MIT
