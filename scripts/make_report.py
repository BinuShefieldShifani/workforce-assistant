"""Turn eval results into eval/REPORT.md.

Usage:
    python scripts/make_report.py eval/results/<v2 run>.json
    python scripts/make_report.py eval/results/<v2 run>.json --baseline eval/results/<v1 run>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from workforce.config import ROOT

METRICS = [
    ("pass_rate", "Cases passed (%)", True),
    ("status_accuracy", "Right outcome: answered / refused / blocked (%)", True),
    ("result_accuracy", "Answered with the right rows (%)", True),
    ("answers_verified_first_time", "Answers whose numbers verified first time (%)", True),
    ("sql_first_try", "SQL accepted on first try (%)", True),
    ("fallbacks", "Fallback answers", False),
    ("style_issues", "Cases with style issues", False),
    ("errors", "Errors", False),
    ("latency_p50_ms", "Latency p50 (ms)", False),
    ("latency_p95_ms", "Latency p95 (ms)", False),
    ("avg_llm_calls", "LLM calls per question", False),
    ("total_output_tokens", "Output tokens, whole run", False),
]

LIMITS = """## What this eval does not show

- **40 questions is a small set.** One case is 2.5 percentage points. Treat
  differences of one or two cases as noise.
- **One run per configuration.** Even at temperature 0 the hosted model is not fully
  deterministic. Repeat runs before trusting a small change.
- **Privacy is enforced in code**, so no run can return a group under 5 people. The
  privacy cases measure whether the *model* behaves sensibly (refuses, writes the
  right filter), not whether data leaks.
- **Result checks compare rows, not wording.** An answer can have the right rows and
  still read badly; the style checks (currency symbols, length) catch only two
  kinds of that.
- **Synthetic org tree.** Team-level numbers say nothing about the real dataset.
"""


def fmt(v) -> str:
    return "–" if v is None else str(v)


def delta(cur, base, higher_better: bool) -> str:
    if cur is None or base is None:
        return "–"
    d = round(cur - base, 1)
    if d == 0:
        return "0"
    better = (d > 0) == higher_better
    return f"{'+' if d > 0 else ''}{d} {'better' if better else 'worse'}"


def render(run: dict, baseline: dict | None) -> str:
    m, s = run["meta"], run["summary"]
    out = ["# Evaluation report", ""]
    out += [f"Model `{m['model']}`, prompts `{m['prompt_version']}` (hash `{m['prompt_hash']}`), "
            f"commit `{m['git_commit']}`, run {m['timestamp']}, golden set hash `{m['golden_hash']}`."]
    if m.get("partial_run"):
        out += ["", "**Partial run: only some cases were included.**"]
    out += ["", "## Summary", ""]

    if baseline:
        bm, bs = baseline["meta"], baseline["summary"]
        out += [f"Compared with `{bm['model']}` / prompts `{bm['prompt_version']}` "
                f"(commit `{bm['git_commit']}`).", ""]
        out += ["| Metric | Baseline | This run | Change |", "|---|---|---|---|"]
        for key, label, hb in METRICS:
            out.append(f"| {label} | {fmt(bs.get(key))} | {fmt(s.get(key))} | "
                       f"{delta(s.get(key), bs.get(key), hb)} |")
    else:
        out += ["| Metric | Value |", "|---|---|"]
        for key, label, _ in METRICS:
            out.append(f"| {label} | {fmt(s.get(key))} |")

    out += ["", f"{s['passed']} of {s['cases']} cases passed. "
                f"{s['result_checked']} answered cases had their result rows checked "
                f"against a hand-written reference query.", ""]

    out += ["## By category", "", "| Category | Passed | Pass rate (%) |", "|---|---|---|"]
    for cat, c in s["by_category"].items():
        out.append(f"| {cat} | {c['passed']}/{c['cases']} | {fmt(c['pass_rate'])} |")

    fails = [r for r in run["results"] if not r["passed"]]
    out += ["", f"## Failures ({len(fails)})", ""]
    if not fails:
        out.append("None.")
    base_by_id = {r["id"]: r for r in baseline["results"]} if baseline else {}
    for r in fails:
        out += [f"### {r['id']} ({r['category']}): {r['question']}", ""]
        out.append(f"- Expected {', '.join(r['expect'])}; got **{r['status']}**")
        if r["result_checked"] and not r["result_ok"]:
            out.append(f"- Result check failed: {r['result_note']}")
        for issue in r["style_issues"]:
            out.append(f"- Style: {issue}")
        if r["sql"]:
            out.append(f"- SQL: `{' '.join(r['sql'].split())}`")
        out.append(f"- Answer: {r['answer'][:400]}")
        if r["id"] in base_by_id:
            out.append(f"- In the baseline: {'passed' if base_by_id[r['id']]['passed'] else 'also failed'}")
        out.append("")

    if baseline:
        fixed = [r["id"] for r in run["results"]
                 if r["passed"] and r["id"] in base_by_id and not base_by_id[r["id"]]["passed"]]
        broke = [r["id"] for r in run["results"]
                 if not r["passed"] and r["id"] in base_by_id and base_by_id[r["id"]]["passed"]]
        out += ["## Changes from the baseline", "",
                f"- Fixed: {', '.join(fixed) or 'none'}",
                f"- Newly failing: {', '.join(broke) or 'none'}", ""]

    out += ["## All cases", "", "| Id | Category | Expected | Got | Rows | Passed | ms |",
            "|---|---|---|---|---|---|---|"]
    for r in run["results"]:
        rows = "–" if not r["result_checked"] else ("ok" if r["result_ok"] else "wrong")
        out.append(f"| {r['id']} | {r['category']} | {', '.join(r['expect'])} | {r['status']} | "
                   f"{rows} | {'yes' if r['passed'] else 'no'} | {r['latency_ms']} |")
    out += ["", LIMITS]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "REPORT.md")
    args = parser.parse_args()

    run = json.loads(args.run.read_text())
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    args.out.write_text(render(run, baseline))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
