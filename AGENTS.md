# Agent Instructions — Aagman QA Harness

## GitHub reporting / issue creation

When the user asks to push a report to GitHub, create an issue, or comment on an issue:

1. Use the CLI push command:
   - Create a new issue:
     ```bash
     aagman-qa push --run-id <RUN_ID> --create-issue
     ```
   - Comment on an existing issue:
     ```bash
     aagman-qa push --run-id <RUN_ID> --issue <ISSUE_NUMBER>
     ```
   - Or publish immediately after a run:
     ```bash
     aagman-qa run --manifest <MANIFEST> --push --create-issue ...
     ```
2. Required environment variables are read from `.env`:
   - `GITHUB_OWNER` (default: `kotilabs`)
   - `GITHUB_REPO` (default: `aagman-v2`)
   - `GITHUB_TOKEN` or `GH_TOKEN`
3. Screenshots are uploaded to a separate public artifacts repo configured by:
   - `AAGMAN_QA_SCREENSHOTS_OWNER` (default: `iamaryansinha`)
   - `AAGMAN_QA_SCREENSHOTS_REPO` (default: `aagman-qa-screenshots`)
   - `AAGMAN_QA_SCREENSHOTS_TOKEN` or `GH_TOKEN_PERSONAL`
4. After pushing, report the issue URL and branch to the user.

## Running tests

- Backtests and research prompts can be run in batch mode to save time:
  ```bash
  aagman-qa run --manifest <MANIFEST> --batch-backtest ...
  aagman-qa run --manifest <MANIFEST> --batch-research ...
  ```
- Chart prompts use `type: chart` and run sequentially through the `chart_query` runner.
