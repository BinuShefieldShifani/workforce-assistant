"""Ask the assistant a question as a given leader.

Usage:
    python scripts/ask.py --list-leaders
    python scripts/ask.py --leader 374 "What is attrition by job role?"
    python scripts/ask.py --leader 374 --json "How many people work overtime?"

Needs an LLM key in .env (see .env.example).
"""

from __future__ import annotations

import argparse
import json
import sys

from workforce.config import DUCKDB_PATH, ROOT
from workforce.scope import list_leaders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--leader", type=int)
    parser.add_argument("--list-leaders", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    if args.list_leaders:
        df = list_leaders(DUCKDB_PATH)
        print(df.groupby("unit_type", sort=False).head(5).to_string(index=False))
        print(f"\n{len(df)} leaders in total")
        return 0
    if args.leader is None or not args.question:
        parser.error("give --leader and a question, or --list-leaders")

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    from workforce.agent import WorkforceAgent
    from workforce.llm import get_llm

    result = WorkforceAgent(DUCKDB_PATH, get_llm()).ask(args.leader, args.question)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    print(f"\nStatus: {result.status}")
    print(f"Answer: {result.answer}\n")
    if result.sql:
        print(f"SQL:    {result.sql}")
    if result.hidden_groups:
        print(f"Hidden groups: {result.hidden_groups}")
    if result.verification:
        v = result.verification
        print(f"Numbers checked: {v['checked']}  unsupported: {v['unsupported']}")
    print("Steps:  " + " | ".join(result.steps))
    print(f"LLM calls: {result.llm_calls}, tokens in/out: "
          f"{result.input_tokens}/{result.output_tokens}, {result.latency_ms} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
