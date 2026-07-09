import argparse
import sys
import time
import uuid
from pathlib import Path

import os

from . import auth, config
from .auth import LoginRequiredError
from .browser import Browser
from .checks import TestResult
from .clarifications import get_answer_provider
from .github_publisher import publish
from .reporter import write_report
from .runners import backtest, charts, options, research

RUNNERS = {
    "backtest": backtest.run,
    "research": research.run,
    "screener": research.run,
    "charts": charts.run,
    "options": options.run,
}


def _run_id(env: str, manifest_name: str) -> str:
    ts = time.strftime("%Y-%m-%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    safe_manifest = manifest_name.replace(" ", "-").replace("/", "-")
    return f"{ts}-{env}-{safe_manifest}-{short}"


def cmd_run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    manifest = config.load_manifest(manifest_path)
    env = args.env or manifest.get("env", "staging")
    base_url = config.get_env_url(env)
    manifest_name = manifest.get("name", manifest_path.stem)
    tests = manifest.get("tests", [])

    if args.test_id:
        tests = [t for t in tests if t.get("id") == args.test_id]
        if not tests:
            print(f"Test id not found in manifest: {args.test_id}", file=sys.stderr)
            return 1

    if not tests:
        print("No tests in manifest.", file=sys.stderr)
        return 1

    run_id = _run_id(env, manifest_name)
    report_dir = Path(args.output_dir) / run_id
    artifact_dir = report_dir / "screenshots"
    report_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    answer_provider = get_answer_provider(args.answer_provider, run_id, report_dir)

    profile = args.profile or config.browser_profile()
    cdp_url = args.cdp_url or config.browser_cdp_url()
    reuse = bool(args.reuse_session)
    session = args.reuse_session or f"aagman-qa-{run_id}"
    browser = Browser(session=session, headed=args.headed, profile=profile, cdp_url=cdp_url, reuse=reuse)

    results: list[TestResult] = []
    try:
        auth.login(browser, base_url, phone=args.phone, otp=args.otp)
        print(f"✅ Logged into {env} ({base_url})")
    except LoginRequiredError as exc:
        ss_path = artifact_dir / "login_required.png"
        try:
            from .checks import capture_failure_screenshot
            capture_failure_screenshot(browser, ss_path)
        except Exception:
            pass
        print(f"\n🚧 {exc}", file=sys.stderr)
        if ss_path.exists():
            print(f"   Screenshot: {ss_path}", file=sys.stderr)
        browser.close()
        return 1

    try:
        for test in tests:
            test_type = test.get("type", "backtest").lower()
            runner = RUNNERS.get(test_type)
            if not runner:
                print(f"Unknown test type: {test_type}", file=sys.stderr)
                continue
            print(f"\n▶ Running {test['id']} ({test_type})...")
            result = runner(browser, base_url, test, artifact_dir, answer_provider=answer_provider)
            results.append(result)
            icon = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "🚧", "ERROR": "⚠️"}.get(result.status, "❓")
            print(f"  {icon} {result.status} — {result.duration_sec}s — {result.message or 'OK'}")
    except Exception as exc:
        print(f"\n⚠️ Harness error: {exc}", file=sys.stderr)
    finally:
        if not reuse:
            browser.close()

    md_path = write_report(run_id, env, base_url, manifest_name, results, report_dir)

    blocked = sum(1 for r in results if r.status == "BLOCKED")
    print(f"\n📄 Report: {md_path}")
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    print(f"📊 {len(results)} tests | ✅ {passed} | ❌ {failed} | 🚧 {blocked}")

    if (failed or blocked) and not args.push:
        print(f"\nTo push after approval: aagman-qa push --run-id {run_id} --create-issue")

    if args.push:
        info = publish(run_id, report_dir, issue_number=args.issue, create_issue=args.create_issue)
        print(f"\n🚀 Published QA report")
        if info.get("report_pushed"):
            print(f"   Branch: {info['owner']}/{info['repo']} `{info['branch']}`")
        if info.get("issue_url"):
            print(f"   Issue: {info['issue_url']}")

    return 0 if (failed == 0 and blocked == 0) else 1


def cmd_answer(args: argparse.Namespace) -> int:
    """Record an agent/human answer for a pending clarification."""
    report_dir = Path(args.report_dir) / args.run_id
    answers_dir = report_dir / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    answer_file = answers_dir / f"{args.test_id}.txt"
    answer_file.write_text(args.text, encoding="utf-8")
    print(f"✅ Answer recorded for {args.run_id}/{args.test_id}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    report_dir = Path(args.report_dir or f"reports/{args.run_id}")
    if not report_dir.exists():
        print(f"Report directory not found: {report_dir}", file=sys.stderr)
        return 1

    info = publish(args.run_id, report_dir, issue_number=args.issue, create_issue=args.create_issue)
    print(f"🚀 Published QA report")
    if info.get("report_pushed"):
        print(f"   Branch: {info['owner']}/{info['repo']} `{info['branch']}`")
    if info.get("issue_url"):
        print(f"   Issue: {info['issue_url']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="aagman-qa", description="Aagman QA harness")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a test manifest")
    run_parser.add_argument("--env", choices=["prod", "staging"], help="Environment override")
    run_parser.add_argument("--manifest", required=True, help="Path to YAML manifest")
    run_parser.add_argument("--headed", action="store_true", default=True, help="Run headed browser (default)")
    run_parser.add_argument("--headless", dest="headed", action="store_false", help="Run headless browser")
    run_parser.add_argument("--profile", help="Use a real Chrome profile (e.g. kotilabs.com). Falls back to BROWSER_USE_PROFILE env var.")
    run_parser.add_argument("--cdp-url", help="Connect to an existing Chrome via CDP (e.g. http://localhost:9222). Falls back to BROWSER_USE_CDP_URL env var.")
    run_parser.add_argument("--reuse-session", help="Reuse an already-running browser-use session instead of starting a new one.")
    run_parser.add_argument("--phone", help="Phone number for login. Falls back to AAGMAN_PHONE env var.")
    run_parser.add_argument("--otp", help="OTP for login. Falls back to AAGMAN_OTP env var.")
    run_parser.add_argument("--output-dir", default="reports", help="Directory for reports")
    run_parser.add_argument("--answer-provider", choices=["manifest", "default", "interactive", "blocked", "llm"], default="interactive", help="How to handle clarification questions from the app")
    run_parser.add_argument("--test-id", help="Run only the test with this id")
    run_parser.add_argument("--push", action="store_true", help="Push results to GitHub after run")
    run_parser.add_argument("--issue", type=int, help="Existing GitHub issue number to comment on")
    run_parser.add_argument("--create-issue", action="store_true", help="Create a new GitHub issue with the report")
    run_parser.set_defaults(func=cmd_run)

    push_parser = sub.add_parser("push", help="Push an existing local report to GitHub")
    push_parser.add_argument("--run-id", required=True, help="Run ID to push")
    push_parser.add_argument("--report-dir", help="Override report directory")
    push_parser.add_argument("--issue", type=int, help="Existing GitHub issue number to comment on")
    push_parser.add_argument("--create-issue", action="store_true", help="Create a new GitHub issue with the report")
    push_parser.set_defaults(func=cmd_push)

    answer_parser = sub.add_parser("answer", help="Provide an answer to a pending clarification")
    answer_parser.add_argument("--run-id", required=True, help="Run ID with the pending clarification")
    answer_parser.add_argument("--test-id", required=True, help="Test ID that is blocked")
    answer_parser.add_argument("--text", required=True, help="Answer text to submit")
    answer_parser.add_argument("--report-dir", default="reports", help="Directory for reports")
    answer_parser.set_defaults(func=cmd_answer)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
