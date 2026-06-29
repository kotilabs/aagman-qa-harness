import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checks import TestResult


def write_report(
    run_id: str,
    env: str,
    base_url: str,
    manifest_name: str,
    results: list[TestResult],
    report_dir: Path,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "base_url": base_url,
        "manifest": manifest_name,
        "total": len(results),
        "pass": sum(1 for r in results if r.status == "PASS"),
        "fail": sum(1 for r in results if r.status == "FAIL"),
        "blocked": sum(1 for r in results if r.status == "BLOCKED"),
        "error": sum(1 for r in results if r.status == "ERROR"),
    }

    # JSON report
    json_path = report_dir / "results.json"
    json_path.write_text(
        json.dumps(
            {**summary, "tests": [asdict(r) for r in results]},
            indent=2,
            default=lambda o: str(o),
        ),
        encoding="utf-8",
    )

    # Markdown report
    md_path = report_dir / "report.md"
    lines = [
        f"# Aagman QA Report — {manifest_name}",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Environment:** {env} ({base_url})",
        f"- **Timestamp:** {summary['timestamp']}",
        f"- **Total:** {summary['total']} | ✅ Pass: {summary['pass']} | ❌ Fail: {summary['fail']} | 🚧 Blocked: {summary['blocked']} | ⚠️ Error: {summary['error']}",
        "",
        "## Summary",
        "",
        "| ID | Status | Duration | Message |",
        "|---|---|---|---|",
    ]
    for r in results:
        icon = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "🚧", "ERROR": "⚠️"}.get(r.status, "❓")
        msg = r.message.replace("|", "\\|") if r.message else "—"
        lines.append(f"| {r.id} | {icon} {r.status} | {r.duration_sec}s | {msg} |")

    lines.extend(["", "## Details", ""])
    for r in results:
        lines.extend([
            f"### {r.id} — {r.status} ({r.duration_sec}s)",
            "",
        ])
        if r.message:
            lines.append(f"**Message:** {r.message}")
            lines.append("")
        if r.logs:
            lines.append("**Logs:**")
            for log in r.logs:
                lines.append(f"- {log}")
            lines.append("")
        if r.screenshots:
            lines.append("**Screenshots:**")
            for ss in r.screenshots:
                rel = ss.relative_to(report_dir) if ss.is_relative_to(report_dir) else ss
                lines.append(f"- `{rel}`")
                lines.append(f"  ![{ss.name}](./{rel})")
            lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
