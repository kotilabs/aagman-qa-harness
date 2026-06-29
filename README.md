# aagman-qa

A CLI QA harness for the [Aagman](https://app.aagman.ai/) web app. It drives a real browser with [`browser-use`](https://github.com/browser-use/browser-use), runs declarative test manifests, screenshots failures, writes local reports, and publishes consolidated results to GitHub — either as issue comments or as new issues.

---

## What it does

| Capability | Description |
|---|---|
| **Backtest regression** | Feeds prompts to Aagman one-per-chat, waits for the report card, and marks PASS/FAIL. |
| **Research / Screener smoke** | Submits screener prompts and checks that a results table loads. |
| **Charts smoke** | Opens Charts, cycles timeframes, adds indicators, and verifies the canvas. |
| **Options smoke** | Loads Option Chain, adds/removes Payoff Builder legs, and checks Vol Surface. |
| **Failure artifacts** | Every failing test captures a full-page screenshot; final report + screenshots are uploaded to a public GitHub artifacts repo so images can be embedded in issues. |
| **GitHub publishing** | One command can comment on an existing issue **or** create a new issue with a standardized report template. |

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
        │           │  (Typer / argparse) │            │
        │           └──────────┬──────────┘            │
        │                      │                       │
   ┌────▼─────┐        ┌───────▼────────┐        ┌─────▼──────┐
   │ Browser  │        │   Runners      │        │  Reporter  │
   │ wrapper  │◄──────►│ (backtest/     │◄──────►│ (markdown  │
   │(browser- │        │ research/      │        │  + json)   │
   │  use)    │        │ charts/        │        └─────┬──────┘
   └────┬─────┘        │ options)       │              │
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

The harness is split into small, replaceable modules:

| File | Responsibility |
|---|---|
| `aagman_qa/cli.py` | Commands: `run`, `push`, `--create-issue`. |
| `aagman_qa/config.py` | Environment variables, manifest loading, run-id generation. |
| `aagman_qa/browser.py` | Wraps `browser-use`: start Chrome, attach to CDP, navigate, click, type, evaluate. |
| `aagman_qa/auth.py` | Phone/OTP login flow. Can be skipped when Chrome is already logged in. |
| `aagman_qa/interactions.py` | Generic chat-input helpers used by multiple runners. |
| `aagman_qa/checks.py` | `TestResult` dataclass, screenshot helpers, success/error heuristics. |
| `aagman_qa/reporter.py` | Writes local `report.md` and `results.json`. |
| `aagman_qa/github_publisher.py` | Uploads screenshots to the artifacts repo and posts the GitHub issue/comment. |
| `aagman_qa/runners/*.py` | Domain-specific test runners. |

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
| `AAGMAN_PHONE` | Your registered phone number. The harness will prompt if not set. |
| `GITHUB_TOKEN` or `GH_TOKEN` | Token for creating issues / posting comments. Needs `repo` scope. |
| `GH_TOKEN_PERSONAL` (or `SCREENSHOTS_TOKEN`) | Token with write access to the public screenshots repo. Used for uploading artifacts. Defaults to `GITHUB_TOKEN` if unset. |

### 4. Optional environment variables

| Var | Purpose |
|---|---|
| `AAGMAN_OTP` | Pre-seed OTP for non-interactive runs. |
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
  https://app.aagman.ai/
```

Log in once inside that window. Later runs reuse the session:

```bash
aagman-qa run --env prod --manifest manifests/backtest-5-prompts.yaml --cdp-url http://localhost:9222 --reuse-session
```

---

## Usage

### Run a manifest

```bash
# Physical browser, reuse an already-logged-in session
aagman-qa run --env prod --manifest manifests/backtest-5-prompts.yaml --cdp-url http://localhost:9222 --reuse-session

# Create a new GitHub issue with the full report + failure screenshots
aagman-qa run --env prod --manifest manifests/backtest-5-prompts.yaml --cdp-url http://localhost:9222 --reuse-session --push --create-issue
```

If the app is logged out, the harness will walk through the phone/OTP login flow.

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
env: prod
tests:
  - id: bt-01
    type: backtest
    prompt: |
      Run a backtest for a long straddle on NIFTY 50 for the last 30 days.
      Entry: buy ATM call and ATM put at 9:20 AM.
      Exit: 3:15 PM or 10% profit / 5% stop loss.
    expected_contains:
      - "TOTAL PNL"
      - "MAX DRAWDOWN"
      - "WIN RATE"
    error_markers:
      - "Cannot run backtest"
      - "Backtest failed"
    timeout: 180

  - id: research-01
    type: research
    prompt: "Find Nifty 50 stocks above 200 SMA with RSI between 50 and 70"
    success_markers:
      - "Found"
      - "Symbol"
      - "LTP"
    timeout: 120

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
```

### Test types

| Type | Fields | Pass criteria |
|---|---|---|
| `backtest` | `prompt`, `expected_contains`, `error_markers`, `timeout` | Report card appears and contains all expected markers. Zero-trades report is OK. |
| `research` / `screener` | `prompt`, `success_markers`, `timeout` | Results table or "Found 0 matches" appears. |
| `charts` | `title`, `description`, `timeframes`, `indicators`, `timeout` | Chart canvas loads; each timeframe is clicked; indicators are added successfully. |
| `options` | `title`, `description`, `timeout` | Chain loads (`PCR`, `Max Pain`, `ATM IV`); payoff builder updates; vol surface renders. |

For charts/options, `title` and `description` are used in the GitHub issue sections when there is no natural-language `prompt`.

---

## How success / failure is decided

| Screen | PASS | FAIL |
|---|---|---|
| **Backtest** | Report card with `TOTAL PNL`, `MAX DRAWDOWN`, `WIN RATE` appears. Even zero trades count as a completed report. | Explicit error message or timeout without the report card. |
| **Research / Screener** | Results table or `Found 0 matches` appears. | Error toast, empty response, spinner timeout. |
| **Charts** | Canvas loads; each requested timeframe is selected; up to 3 indicators can be added. | Canvas missing, timeframe hangs, indicator not added. |
| **Options** | Chain loads (`PCR`, `Max Pain`, `ATM IV`); Payoff Builder updates after adding/removing legs; Vol Surface renders. | Spinner timeout, empty chain, leg change not reflected, surface missing. |

Failure screenshots are scrolled to the bottom of the main scrollable pane before capture so the latest chat / error is visible.

---

## GitHub issue template

Generated issues follow this structure:

```markdown
## QA Run Summary

- **Date:** 2026-06-24
- **Tester:** Arjun
- **Platform:** prod
- **Result:** 3 / 5 passed

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
│   ├── cli.py                 # Typer/argparse commands
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
│       └── options.py
├── manifests/
│   ├── backtest-5-prompts.yaml
│   ├── backtest-10-prompts.yaml
│   ├── research-smoke.yaml
│   └── smoke-charts-options.yaml
├── reports/                   # generated, gitignored
├── .gitignore
├── .env.example
├── pyproject.toml
└── README.md
```

Generated artifacts (`reports/`, `.chrome-data*`, `.venv/`, `__pycache__/`, `.env`) are excluded from git.

---

## Development notes

- The harness uses **browser-use** over CDP because Aagman authentication and certain shadow-DOM inputs are already known to work reliably through browser-use.
- Each run gets a unique `run-id` and writes to `reports/<run-id>/`.
- Screenshots are captured as absolute paths (browser-use requires this) and later converted to relative references in the local report.
- The screenshots repo is treated as a separate, public artifact store so failure images can be embedded inline in issues even when the primary repo does not grant push access.

---

## Roadmap / known gaps

- [ ] Stabilize charts/options selectors once the app UI stabilizes.
- [ ] Add retry logic for transient bot-detection or network stalls.
- [ ] Support parallel test execution across multiple browser sessions.
- [ ] Add a `--dry-run` mode that validates manifests without opening the browser.
- [ ] Backfill unit tests for manifest parsing and reporter output.

---

## License

MIT
