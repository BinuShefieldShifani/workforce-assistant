"""Golden-set evaluation.

Each golden case names a leader (by role, e.g. "dept_rd"), a question, the
statuses that count as correct, and optionally a hand-written reference SQL.

For answered cases we compare the agent's *result rows* with the reference
rows, both passed through the same privacy rules. Comparing results instead of
SQL text means different but equivalent queries still pass, while a query that
answers a slightly different question (the team <> 'X' NULL bug) fails, even
though its numbers pass the answer verifier.

Placeholders like {small_team_rd} are filled from the database, so the golden
file works on any build of the org tree, including the fake data used in CI.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb
import yaml

from workforce.agent import AgentResult, WorkforceAgent
from workforce.guard import GROUP_SIZE_COL, guard
from workforce.privacy import QueryBlocked, run_safe
from workforce.scope import ScopedDB, get_leader

# Named lookups used in golden.yaml. Each returns one value.
PLACEHOLDER_SQL = {
    "ceo": "SELECT head_employee_id FROM org_units WHERE unit_type = 'company'",
    "dept_rd": "SELECT head_employee_id FROM org_units WHERE name = 'Research & Development'",
    "dept_sales": "SELECT head_employee_id FROM org_units WHERE name = 'Sales'",
    "dept_hr": "SELECT head_employee_id FROM org_units WHERE name = 'Human Resources'",
    "func_sales_exec": "SELECT head_employee_id FROM org_units WHERE name = 'Sales / Sales Executive'",
    "tiny": "SELECT leader_id FROM leaders WHERE headcount < 5 ORDER BY leader_id LIMIT 1",
    "small_team_rd": """
        SELECT u.name FROM org_units u JOIN unit_headcount h USING (org_unit_id)
        WHERE u.unit_type = 'team' AND u.name LIKE 'Research & Development /%'
          AND h.headcount < 5 ORDER BY u.name LIMIT 1""",
    "big_team_rd": """
        SELECT u.name FROM org_units u JOIN unit_headcount h USING (org_unit_id)
        WHERE u.unit_type = 'team' AND u.name LIKE 'Research & Development /%'
          AND h.headcount >= 10 ORDER BY u.name LIMIT 1""",
}

CURRENCY = re.compile(r"[$€£¥]|\bSEK\b|\bUSD\b|\bkr\b", flags=re.IGNORECASE)
ANSWERED = {"answered", "fallback"}


@dataclass
class GoldenCase:
    id: str
    category: str
    leader: str
    question: str
    expect: list[str]
    reference_sql: str | None = None
    max_words: int = 120
    group: str | None = None   # paraphrase group: variants of one question share a group
    source: str | None = None  # where the question comes from (KPI list, dataset, ...)


def load_golden_files(paths: list[Path]) -> list[GoldenCase]:
    cases = [c for p in paths for c in load_golden(p)]
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate case ids across golden files: {dupes}")
    return cases


def load_golden(path: Path) -> list[GoldenCase]:
    data = yaml.safe_load(Path(path).read_text())
    cases = []
    for raw in data["cases"]:
        expect = raw["expect"]
        cases.append(GoldenCase(
            id=raw["id"],
            category=raw["category"],
            leader=raw["leader"],
            question=raw["question"],
            expect=[expect] if isinstance(expect, str) else list(expect),
            reference_sql=raw.get("reference_sql"),
            max_words=raw.get("max_words", 120),
            group=raw.get("group"),
            source=raw.get("source"),
        ))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in golden file")
    return cases


def resolve_placeholders(db_path: Path) -> dict[str, object]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        values = {}
        for name, sql in PLACEHOLDER_SQL.items():
            row = con.execute(sql).fetchone()
            values[name] = row[0] if row else None
        return values
    finally:
        con.close()


def fill(text: str | None, values: dict) -> str | None:
    if text is None:
        return None
    missing = [k for k in re.findall(r"\{(\w+)\}", text) if values.get(k) is None]
    if missing:
        raise KeyError(f"placeholder(s) not available in this database: {missing}")
    return text.format(**values)


# ---------- comparing result rows ----------

def _norm(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)  # numpy / decimal types
    except (TypeError, ValueError):
        return str(v).strip().lower()


def _values_equal(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return a is b
    if isinstance(a, float) and isinstance(b, float):
        tol = 0.051 + 1e-4 * max(abs(a), abs(b))  # rounding to 1 decimal, tiny relative slack
        return abs(a - b) <= tol or abs(a * 100 - b) <= tol or abs(a - b * 100) <= tol
    return a == b


def _row_contains(agent_row: list, ref_row: list) -> bool:
    """Every reference value appears in the agent row (extra agent columns are fine)."""
    used = set()
    for v in ref_row:
        for j, a in enumerate(agent_row):
            if j not in used and _values_equal(v, a):
                used.add(j)
                break
        else:
            return False
    return True


def _is_label(v) -> bool:
    return not isinstance(_norm(v), float)


def _strict_match(agent: list[list], ref: list[list]) -> bool:
    free = list(range(len(agent)))
    for r in ref:
        match = next((j for j in free if _row_contains(agent[j], r)), None)
        if match is None:
            return False
        free.remove(match)
    return True


def _relabel_match(agent_rows: list[dict], ref_rows: list[dict]) -> dict | None:
    """Match rows on their numbers (including group_size) and allow group labels to be
    renamed, as long as the renaming is consistent: e.g. attrition false -> 'Stayers'
    on every row. Returns the label mapping, or None if no consistent match exists."""
    agent = [list(r.values()) for r in agent_rows]
    ref = [list(r.values()) for r in ref_rows]
    mapping: dict = {}
    used_targets: set = set()
    free = list(range(len(agent)))
    for r in ref:
        nums = [v for v in r if not _is_label(v)]
        labels = [_norm(v) for v in r if _is_label(v)]
        found = None
        for j in free:
            a = agent[j]
            if not _row_contains(a, nums):
                continue
            a_labels = [_norm(v) for v in a if _is_label(v)]
            trial, trial_used, ok = dict(mapping), set(used_targets), True
            for lab in labels:
                if lab in trial:
                    if trial[lab] not in a_labels:
                        ok = False
                        break
                    continue
                target = next((t for t in a_labels if t not in trial_used), None)
                if target is None:
                    ok = False
                    break
                trial[lab], _ = target, trial_used.add(target)
            if ok:
                found, mapping, used_targets = j, trial, trial_used
                break
        if found is None:
            return None
        free.remove(found)
    return mapping


def compare_rows(agent_rows: list[dict], ref_rows: list[dict]) -> tuple[bool, str]:
    if len(agent_rows) != len(ref_rows):
        return False, f"row count {len(agent_rows)} vs reference {len(ref_rows)}"
    agent = [[v for k, v in r.items() if k != GROUP_SIZE_COL] for r in agent_rows]
    ref = [[v for k, v in r.items() if k != GROUP_SIZE_COL] for r in ref_rows]
    if _strict_match(agent, ref):
        return True, "match"
    mapping = _relabel_match(agent_rows, ref_rows)
    if mapping:
        renamed = ", ".join(f"{k}->{v}" for k, v in mapping.items() if k != v)
        return True, f"match with renamed labels ({renamed})" if renamed else "match"
    first_missing = next((r for r in ref if not any(_row_contains(a, r) for a in agent)), ref_rows[0])
    return False, f"reference row not found: {first_missing}"


def run_reference(db_path: Path, leader_id: int, sql: str) -> tuple[str, list[dict]]:
    """Run the hand-written SQL through the same guard and privacy rules as the agent."""
    leader = get_leader(db_path, leader_id)
    db = ScopedDB.for_leader(db_path, leader)
    try:
        res = run_safe(db, guard(sql))
    except QueryBlocked:
        return "blocked", []
    finally:
        db.close()
    rows = res.rows.to_dict(orient="records")
    return ("answered" if rows else "no_data"), rows


# ---------- running and scoring ----------

@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    leader_id: int
    group: str | None
    expect: list[str]
    status: str
    status_ok: bool
    result_checked: bool
    result_ok: bool | None
    result_note: str
    style_issues: list[str]
    passed: bool
    answer: str
    sql: str | None
    hidden_groups: int
    sql_first_try: bool
    verified: bool | None
    llm_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    steps: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    reference_rows: list[dict] = field(default_factory=list)


def score_case(case: GoldenCase, leader_id: int, question: str, res: AgentResult,
               reference: tuple[str, list[dict]] | None) -> CaseResult:
    status_ok = res.status in case.expect

    result_checked = reference is not None and res.status in ANSWERED and reference[0] == "answered"
    result_ok, note = None, ""
    if result_checked:
        result_ok, note = compare_rows(res.rows, reference[1])
    elif reference is not None and reference[0] != "answered" and res.status in ANSWERED:
        result_checked, result_ok = True, False
        note = f"reference is {reference[0]} but the agent answered"

    style = []
    if res.status in ANSWERED:
        if CURRENCY.search(res.answer):
            style.append("currency symbol in answer")
        words = len(res.answer.split())
        if words > case.max_words:
            style.append(f"answer is {words} words (max {case.max_words})")

    passed = status_ok and (result_ok is not False) and not style
    return CaseResult(
        id=case.id, category=case.category, question=question, leader_id=leader_id,
        group=case.group,
        expect=case.expect, status=res.status, status_ok=status_ok,
        result_checked=result_checked, result_ok=result_ok, result_note=note,
        style_issues=style, passed=passed, answer=res.answer, sql=res.sql,
        hidden_groups=res.hidden_groups,
        sql_first_try=res.sql is not None and not any("attempt 1 rejected" in s for s in res.steps),
        verified=(res.verification or {}).get("passed") if res.status in ANSWERED else None,
        llm_calls=res.llm_calls, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
        latency_ms=res.latency_ms, steps=res.steps,
        rows=res.rows, reference_rows=reference[1] if reference else [],
    )


def run_eval(agent: WorkforceAgent, cases: list[GoldenCase], delay_s: float = 0.0,
             rate_limit_wait_s: float = 60.0, log=print) -> list[CaseResult]:
    values = resolve_placeholders(agent.db_path)
    results = []
    for n, case in enumerate(cases, 1):
        leader_id = int(values[case.leader])
        question = fill(case.question, values)
        ref_sql = fill(case.reference_sql, values)
        reference = run_reference(agent.db_path, leader_id, ref_sql) if ref_sql else None

        res = agent.ask(leader_id, question)
        for _ in range(2):  # free tiers rate-limit; wait and retry rather than score an outage
            if res.status == "error" and any("RateLimit" in s for s in res.steps):
                log(f"  rate limited, waiting {rate_limit_wait_s:.0f}s")
                time.sleep(rate_limit_wait_s)
                res = agent.ask(leader_id, question)

        cr = score_case(case, leader_id, question, res, reference)
        results.append(cr)
        mark = "PASS" if cr.passed else "FAIL"
        log(f"[{n:2d}/{len(cases)}] {mark} {case.id} {res.status} ({res.latency_ms} ms)")
        if delay_s:
            time.sleep(delay_s)
    return results


def _pct(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    answered = [r for r in results if r.status in ANSWERED]
    checked = [r for r in results if r.result_checked]
    sql_cases = [r for r in results if r.sql is not None]
    lat = sorted(r.latency_ms for r in results if r.llm_calls)
    by_cat: dict[str, dict] = {}
    for r in results:
        c = by_cat.setdefault(r.category, {"cases": 0, "passed": 0})
        c["cases"] += 1
        c["passed"] += r.passed
    for c in by_cat.values():
        c["pass_rate"] = _pct(c["passed"], c["cases"])
    groups: dict[str, list[bool]] = {}
    for r in results:
        if r.group:
            groups.setdefault(r.group, []).append(r.passed)
    consistent = sum(all(v) for v in groups.values())
    return {
        "cases": n,
        "paraphrase_groups": len(groups),
        "paraphrase_consistency": _pct(consistent, len(groups)),
        "passed": sum(r.passed for r in results),
        "pass_rate": _pct(sum(r.passed for r in results), n),
        "status_accuracy": _pct(sum(r.status_ok for r in results), n),
        "result_accuracy": _pct(sum(bool(r.result_ok) for r in checked), len(checked)),
        "result_checked": len(checked),
        "answers_verified_first_time": _pct(sum(r.status == "answered" for r in answered), len(answered)),
        "fallbacks": sum(r.status == "fallback" for r in results),
        "sql_first_try": _pct(sum(r.sql_first_try for r in sql_cases), len(sql_cases)),
        "style_issues": sum(bool(r.style_issues) for r in results),
        "errors": sum(r.status == "error" for r in results),
        "latency_p50_ms": int(statistics.median(lat)) if lat else None,
        "latency_p95_ms": lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)] if lat else None,
        "avg_llm_calls": round(sum(r.llm_calls for r in results) / n, 2) if n else None,
        "total_input_tokens": sum(r.input_tokens for r in results),
        "total_output_tokens": sum(r.output_tokens for r in results),
        "by_category": by_cat,
    }


def results_to_dict(results: list[CaseResult]) -> list[dict]:
    return [asdict(r) for r in results]
