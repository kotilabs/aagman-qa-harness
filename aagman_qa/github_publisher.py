import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config


def _gh(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub CLI (gh) not found on PATH")
    env = os.environ.copy()
    env["GH_TOKEN"] = config.github_token() or env.get("GH_TOKEN", "")
    result = subprocess.run(
        [gh] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _git(args: list[str], cwd: Path, env: dict, timeout: int = 120) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git not found on PATH")
    result = subprocess.run(
        [git] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _default_branch(owner: str, repo: str) -> str:
    out = _gh(["api", f"repos/{owner}/{repo}", "--jq", ".default_branch"])
    return out or "main"


def upload_artifacts(run_id: str, report_dir: Path) -> dict:
    """Push report artifacts (screenshots + report.md + results.json) to a public
    GitHub repo so they can be embedded in issue bodies even when the primary
    repo cannot be written to.
    """
    owner = config.screenshots_owner()
    repo = config.screenshots_repo()
    token = config.screenshots_token()
    if not (token and owner and repo):
        return {}

    repo_path = f"{owner}/{repo}"
    remote_url = f"https://x-access-token:{token}@github.com/{repo_path}.git"
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    screenshot_dir = report_dir / "screenshots"
    if not screenshot_dir.exists():
        return {}

    with tempfile.TemporaryDirectory(prefix="aagman_qa_artifacts_") as tmpdir:
        clone_dir = Path(tmpdir) / "repo"
        _git(["clone", "--depth=1", remote_url, str(clone_dir)], cwd=Path(tmpdir), env=env)

        dest = clone_dir / run_id
        dest.mkdir(parents=True, exist_ok=True)

        # Copy screenshots.
        screenshot_urls = {}
        for src in screenshot_dir.glob("*.png"):
            shutil.copy2(src, dest / src.name)
            screenshot_urls[src.stem] = (
                f"https://raw.githubusercontent.com/{repo_path}/main/{run_id}/{src.name}"
            )

        # Copy report text files.
        for name in ("report.md", "results.json"):
            src = report_dir / name
            if src.exists():
                shutil.copy2(src, dest / name)

        _git(["add", run_id], cwd=clone_dir, env=env)

        # Only commit/push if there are changes.
        git = shutil.which("git")
        diff = subprocess.run(
            [git, "diff", "--cached", "--quiet"],
            cwd=clone_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if diff.returncode != 0:
            _git(
                [
                    "-c", "user.email=aagman-qa@example.com",
                    "-c", "user.name=Aagman QA Bot",
                    "commit", "-m", f"qa: artifacts for {run_id}",
                ],
                cwd=clone_dir,
                env=env,
            )
            _git(["push", "origin", "HEAD:main"], cwd=clone_dir, env=env)

    report_url = f"https://raw.githubusercontent.com/{repo_path}/main/{run_id}/report.md"
    return {
        "owner": owner,
        "repo": repo,
        "report_url": report_url,
        "screenshot_urls": screenshot_urls,
    }


def publish(
    run_id: str,
    report_dir: Path,
    issue_number: int | None = None,
    create_issue: bool = False,
) -> dict:
    owner = config.github_owner()
    repo = config.github_repo()
    token = config.github_token()
    repo_path = f"{owner}/{repo}"

    # 1. Always upload screenshots/artifacts to the public screenshots repo first.
    artifacts = upload_artifacts(run_id, report_dir)

    # 2. Try to push report files to the primary repo (best-effort).
    branch = f"qa-run-{run_id.replace('/', '-')}".lower()
    report_pushed = False
    if token:
        try:
            _push_to_primary_repo(run_id, report_dir, owner, repo, branch)
            report_pushed = True
        except Exception as exc:
            print(f"  ⚠️ Could not push to {repo_path}: {exc}")
    else:
        print(f"  ⚠️ No GitHub token configured; skipping primary repo push.")

    # 3. Build issue/comment body.
    body = _build_issue_body(run_id, report_dir, owner, repo, branch, artifacts, report_pushed)

    # 4. Post to GitHub.
    issue_url = None
    if issue_number:
        body_file = report_dir / "github_comment.md"
        body_file.write_text(body, encoding="utf-8")
        _gh([
            "issue", "comment", str(issue_number),
            "--repo", repo_path,
            "--body-file", str(body_file),
        ])
        issue_url = f"https://github.com/{repo_path}/issues/{issue_number}"
    elif create_issue:
        body_file = report_dir / "github_issue_body.md"
        body_file.write_text(body, encoding="utf-8")
        out = _gh([
            "issue", "create",
            "--repo", repo_path,
            "--title", f"QA Report: {run_id}",
            "--body-file", str(body_file),
        ])
        issue_url = out.strip()

    return {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "report_path": f"qa-reports/{run_id}/report.md",
        "issue_number": issue_number,
        "issue_url": issue_url,
        "artifacts": artifacts,
        "report_pushed": report_pushed,
    }


def _push_to_primary_repo(
    run_id: str,
    report_dir: Path,
    owner: str,
    repo: str,
    branch: str,
) -> None:
    token = config.github_token()
    if not token:
        raise RuntimeError("GITHUB_TOKEN / GH_TOKEN not configured")

    repo_path = f"{owner}/{repo}"
    remote_url = f"https://x-access-token:{token}@github.com/{repo_path}.git"
    env = os.environ.copy()
    env["GH_TOKEN"] = token

    with tempfile.TemporaryDirectory(prefix="aagman_qa_") as tmpdir:
        clone_dir = Path(tmpdir) / "repo"
        _git(["clone", "--depth=1", remote_url, str(clone_dir)], cwd=Path(tmpdir), env=env)
        _git(["checkout", "-b", branch], cwd=clone_dir, env=env)

        dest = clone_dir / "qa-reports" / run_id
        dest.mkdir(parents=True, exist_ok=True)
        for src in report_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(report_dir)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)

        _git(["add", "qa-reports"], cwd=clone_dir, env=env)
        _git(
            [
                "-c", "user.email=aagman-qa@example.com",
                "-c", "user.name=Aagman QA Bot",
                "commit", "-m", f"qa: report {run_id}",
            ],
            cwd=clone_dir,
            env=env,
        )
        _git(["push", "-u", "origin", branch], cwd=clone_dir, env=env)


def _build_issue_body(
    run_id: str,
    report_dir: Path,
    owner: str,
    repo: str,
    branch: str,
    artifacts: dict,
    report_pushed: bool,
) -> str:
    results_file = report_dir / "results.json"
    summary = json.loads(results_file.read_text(encoding="utf-8")) if results_file.exists() else {}
    repo_path = f"{owner}/{repo}"

    if report_pushed:
        report_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/qa-reports/{run_id}/report.md"
    else:
        report_url = artifacts.get("report_url", "")

    date_str = _format_date(summary.get("timestamp", ""))
    tester = config.tester_name()
    platform = summary.get("base_url", "aagman.ai")
    total = summary.get("total", 0)
    passed = summary.get("pass", 0)
    failed = summary.get("fail", 0)
    blocked = summary.get("blocked", 0)

    lines = [
        f"**Date:** {date_str}  ",
        f"**Tester:** {tester}  ",
        f"**Platform:** {platform} chat backtest  ",
        f"**Result:** {passed} PASS, {failed} FAIL{"" if not blocked else f', {blocked} BLOCKED'} out of {total} tests",
        "",
        "---",
        "",
        "## Summary",
        "",
        _generate_summary(summary),
        "",
    ]

    tests = summary.get("tests", [])
    screenshot_urls = artifacts.get("screenshot_urls", {})

    for idx, t in enumerate(tests, 1):
        lines.extend(_build_test_section(idx, t, screenshot_urls))
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Reproduction",
        "",
        f"Run each prompt in a fresh Backtest workspace on `{platform}`. The harness follows the flow: submit prompt → click **Run Risk Checks** → click **Run Backtest**.",
        "",
        "---",
        "",
        "## Test-by-Test Results",
        "",
        "| # | Prompt | Result |",
        "|---|---|---|",
    ])
    for t in tests:
        prompt_preview = (t.get("prompt") or t["id"]).replace("|", "\\|")[:120]
        if len(prompt_preview) == 120:
            prompt_preview += "..."
        status = t.get("status", "UNKNOWN")
        lines.append(f"| {t['id']} | {prompt_preview} | **{status}** |")

    return "\n".join(lines)


def _format_date(iso_timestamp: str) -> str:
    if not iso_timestamp:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from datetime import datetime
        # Handles both ISO with offset and Z.
        ts = iso_timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except Exception:
        return iso_timestamp[:10]


def _generate_summary(summary: dict) -> str:
    tests = summary.get("tests", [])
    failures = [t for t in tests if t.get("status") == "FAIL"]
    passes = [t for t in tests if t.get("status") == "PASS"]
    parts = []
    if passes:
        parts.append(f"{len(passes)} test(s) passed and produced valid report cards.")
    if failures:
        parts.append(f"{len(failures)} test(s) failed — see per-test analysis below.")
    if not parts:
        parts.append("No tests were run.")
    return " ".join(parts)


def _build_test_section(idx: int, test: dict, screenshot_urls: dict) -> list[str]:
    test_id = test["id"]
    status = test.get("status", "UNKNOWN")
    message = (test.get("message") or "—").replace("|", "\\|")
    screenshot_url = next(
        (u for name, u in screenshot_urls.items() if name.startswith(test_id)),
        None,
    )

    severity = "—"
    if status == "FAIL":
        # Default severity; human can refine in the issue.
        severity = "Medium"

    lines = [
        f"## {idx}. {test_id}",
        "",
        "| | |",
        "|---|---|",
        f"| **Test** | {test_id} |",
    ]

    prompt = test.get("prompt", "")
    title = test.get("title", "")
    description = test.get("description", "")

    if prompt:
        lines.append(f"| **Prompt** | `{prompt.replace('|', '\\|')}` |")
    else:
        if title:
            lines.append(f"| **Title** | {title.replace('|', '\\|')} |")
        if description:
            lines.append(f"| **Description** | {description.replace('|', '\\|')} |")

    lines.extend([
        f"| **Aagman result** | **{status}** — {message} |",
        "| **External verification / analysis** | _To be filled by tester._ |",
        f"| **Assessment** | **{status}** |",
        f"| **Severity** | {severity} |",
    ])

    if screenshot_url:
        lines.extend([
            "",
            f"![{test_id}]({screenshot_url})",
        ])

    return lines
