"""Day 4: run the golden question set against the real LLM and save the results.

Usage:
    python scripts/run_eval.py --prompt-version v1          # baseline
    python scripts/run_eval.py --prompt-version v2          # after the prompt fixes
    python scripts/run_eval.py --ids b03 p06 --delay 0      # re-run a few cases

Writes eval/results/<timestamp>_<model>_<prompt>.json. Turn it into a report with
scripts/make_report.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from workforce.config import DUCKDB_PATH, ROOT

GOLDEN = ROOT / "eval" / "golden.yaml"
RESULTS_DIR = ROOT / "eval" / "results"


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--prompt-version", default=None, help="v1 or v2 (default: PROMPT_VERSION or v2)")
    parser.add_argument("--ids", nargs="*", help="only run these case ids")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="seconds between cases, to stay under free-tier rate limits")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    from workforce.agent import WorkforceAgent
    from workforce.evaluation import load_golden, results_to_dict, run_eval, summarize
    from workforce.llm import get_llm

    cases = load_golden(args.golden)
    if args.ids:
        cases = [c for c in cases if c.id in set(args.ids)]
        if not cases:
            parser.error("none of those ids are in the golden file")

    llm = get_llm()
    agent = WorkforceAgent(DUCKDB_PATH, llm, prompt_version=args.prompt_version)
    print(f"Model {llm.model}, prompts {agent.prompts.version} ({agent.prompts.hash}), "
          f"{len(cases)} cases\n")

    started = datetime.now(timezone.utc)
    results = run_eval(agent, cases, delay_s=args.delay)
    summary = summarize(results)

    meta = {
        "timestamp": started.isoformat(timespec="seconds"),
        "model": llm.model,
        "prompt_version": agent.prompts.version,
        "prompt_hash": agent.prompts.hash,
        "git_commit": git_commit(),
        "golden_file": str(args.golden.relative_to(ROOT)) if args.golden.is_relative_to(ROOT)
        else str(args.golden),
        "golden_hash": hashlib.sha256(args.golden.read_bytes()).hexdigest()[:10],
        "partial_run": bool(args.ids),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = llm.model.replace("/", "-")
    out = RESULTS_DIR / f"{started:%Y%m%d-%H%M%S}_{safe_model}_{agent.prompts.version}.json"
    out.write_text(json.dumps({"meta": meta, "summary": summary,
                               "results": results_to_dict(results)}, indent=2, default=str))

    print(f"\nPassed {summary['passed']}/{summary['cases']} ({summary['pass_rate']}%)")
    print(f"Status accuracy {summary['status_accuracy']}%, result accuracy "
          f"{summary['result_accuracy']}% of {summary['result_checked']} checked")
    print(f"Latency p50 {summary['latency_p50_ms']} ms, p95 {summary['latency_p95_ms']} ms")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
