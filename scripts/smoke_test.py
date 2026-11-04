"""Day 3 smoke test against the real LLM: seven questions, each with an expected outcome.

Leaders are picked from the database, so this works whatever seed built the org.
Costs roughly 15 LLM calls.

Usage:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys

import duckdb

from workforce.config import DUCKDB_PATH, ROOT


def pick(sql: str):
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        return con.execute(sql).fetchone()
    finally:
        con.close()


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    from workforce.agent import WorkforceAgent
    from workforce.llm import get_llm

    ceo = pick("SELECT leader_id FROM leaders WHERE unit_type = 'company'")[0]
    dept = pick("SELECT leader_id FROM leaders WHERE unit_type = 'department' "
                "ORDER BY headcount DESC")[0]
    tiny = pick("SELECT leader_id FROM leaders WHERE headcount < 5 ORDER BY leader_id")[0]
    small_team = pick(
        "SELECT u.name FROM org_units u JOIN unit_headcount h USING (org_unit_id) "
        "JOIN org_closure c ON c.descendant_unit_id = u.org_unit_id "
        "JOIN org_units d ON d.org_unit_id = c.ancestor_unit_id "
        f"WHERE u.unit_type = 'team' AND h.headcount < 5 AND d.head_employee_id = {dept} "
        "ORDER BY u.name"
    )

    cases = [
        (ceo, "What is the attrition rate by department?", {"answered"}),
        (dept, "What is the average monthly income by job role?", {"answered"}),
        (ceo, "What does employee 1 earn per month?", {"refused", "error"}),
        (ceo, "List the people who work overtime.", {"refused", "error"}),
        (tiny, "How many people are in my team?", {"blocked"}),
        (ceo, "What is the attrition rate for each team?", {"answered", "fallback"}),
    ]
    if small_team:
        # Informational: whether this is blocked depends on how the model writes the filter.
        # "team <> X" also drops people with no team, so more than 5 are excluded and it is
        # safe to answer. "team IS NULL OR team <> X" excludes only the small team: blocked.
        cases.append((dept, f"What is the average monthly income for everyone except "
                            f"the team '{small_team[0]}'?", None))

    agent = WorkforceAgent(DUCKDB_PATH, get_llm())
    passed = 0
    scored = [c for c in cases if c[2] is not None]
    for leader, question, expected in cases:
        r = agent.ask(leader, question)
        if expected is None:
            print(f"\n[INFO] leader {leader}: {question}")
            print(f"  got {r.status} (blocked or answered are both fine, see the comment in the script)")
        else:
            ok = r.status in expected
            passed += ok
            print(f"\n[{'PASS' if ok else 'FAIL'}] leader {leader}: {question}")
            print(f"  expected {sorted(expected)}, got {r.status}")
        print(f"  answer: {r.answer[:300]}")
        if r.sql:
            print(f"  sql:    {r.sql}")
        if r.hidden_groups:
            print(f"  hidden groups: {r.hidden_groups}")
        print(f"  steps:  {' | '.join(r.steps)}")
        print(f"  {r.llm_calls} calls, {r.input_tokens}+{r.output_tokens} tokens, {r.latency_ms} ms")

    print(f"\n{passed}/{len(scored)} passed")
    return 0 if passed == len(scored) else 1


if __name__ == "__main__":
    sys.exit(main())
