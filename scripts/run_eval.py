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
    parser.add_argument("--golden", type=Path, nargs="+", default=[GOLDEN],
                        help="one or more golden files, run together")
    parser.add_argument("--prompt-version", default=None,
                        help="v1-v4 (default: PROMPT_VERSION, else v4)")
    parser.add_argument("--no-fallback", action="store_true",
                        help="don't retry with the fallback prompt version (default v3)")
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
    from workforce.evaluation import load_golden_files, results_to_dict, run_eval, summarize
    from workforce.llm import get_llm

    cases = load_golden_files(args.golden)
    if args.ids:
        cases = [c for c in cases if c.id in set(args.ids)]
        if not cases:
            parser.error("none of those ids are in the golden file")

    llm = get_llm()
    agent = WorkforceAgent(DUCKDB_PATH, llm, prompt_version=args.prompt_version,
                           fallback_version="none" if args.no_fallback else None)
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
        "fallback_prompt_version": agent.fallback_prompts.version if agent.fallback_prompts else None,
        "git_commit": git_commit(),
        "golden_file": ", ".join(
            str(g.resolve().relative_to(ROOT)) if g.resolve().is_relative_to(ROOT) else str(g)
            for g in args.golden),
        "golden_hash": hashlib.sha256(b"".join(g.read_bytes() for g in args.golden)).hexdigest()[:10],
        "partial_run": bool(args.ids),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = llm.model.replace("/", "-")
    label = "golden" if args.golden == [GOLDEN] else "-".join(g.stem for g in args.golden)
    out = RESULTS_DIR / f"{started:%Y%m%d-%H%M%S}_{safe_model}_{agent.prompts.version}_{label}.json"
    out.write_text(json.dumps({"meta": meta, "summary": summary,
                               "results": results_to_dict(results)}, indent=2, default=str))

    print(f"\nPassed {summary['passed']}/{summary['cases']} ({summary['pass_rate']}%)")
    print(f"Status accuracy {summary['status_accuracy']}%, result accuracy "
          f"{summary['result_accuracy']}% of {summary['result_checked']} checked")
    if summary["paraphrase_groups"]:
        print(f"Paraphrase groups fully passed: {summary['paraphrase_consistency']}% "
              f"of {summary['paraphrase_groups']}")
    print(f"Latency p50 {summary['latency_p50_ms']} ms, p95 {summary['latency_p95_ms']} ms")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
