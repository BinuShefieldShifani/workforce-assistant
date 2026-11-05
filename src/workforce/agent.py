"""The question-answering agent.

    question
      -> LLM writes SQL (or refuses)            up to 2 attempts if the guard
      -> SQL guard                              or the database rejects it
      -> run in the leader's scoped database
      -> privacy rules (small groups, differencing)
      -> LLM writes the answer from the result  up to 2 attempts if
      -> verify every number against the result verification fails
      -> if still unverified: a plain, deterministic answer built from the table

Every step is recorded in AgentResult, so the API and the eval can show
exactly what happened.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from workforce.config import MIN_GROUP_SIZE
from workforce.guard import GROUP_SIZE_COL, SQLRejected, guard
from workforce.llm import LLM
from workforce.privacy import QueryBlocked, SafeResult, run_safe
from workforce.prompts import PromptSet, get_prompts
from workforce.scope import Leader, ScopedDB, get_leader
from workforce.verify import Verification, verify_answer

MAX_SQL_ATTEMPTS = 2
MAX_ANSWER_ATTEMPTS = 2


@dataclass
class AgentResult:
    question: str
    leader_id: int
    status: str  # answered | fallback | refused | blocked | no_data | error
    answer: str
    sql: str | None = None
    rows: list[dict] = field(default_factory=list)
    hidden_groups: int = 0
    verification: dict | None = None
    steps: list[str] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def parse_sql_reply(text: str) -> dict:
    """Pull the JSON object out of the reply, tolerating code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("reply did not contain JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict) or not ({"sql", "refuse"} & data.keys()):
        raise ValueError("JSON must contain 'sql' or 'refuse'")
    return data


def table_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no rows)"
    return df.to_csv(index=False)


def fallback_answer(rows: pd.DataFrame, hidden: int) -> str:
    """No LLM: just state the result, so every number is guaranteed correct."""
    parts = []
    for _, row in rows.head(10).iterrows():
        parts.append(", ".join(f"{k}: {v}" for k, v in row.items()))
    text = "Here is the result: " + "; ".join(parts) + "."
    if len(rows) > 10:
        text += f" ({len(rows) - 10} more rows not shown.)"
    if hidden:
        text += f" {hidden} group(s) were hidden for privacy."
    return text


class WorkforceAgent:
    def __init__(self, db_path: Path, llm: LLM, prompt_version: str | None = None):
        self.db_path = Path(db_path)
        self.llm = llm
        self.prompts: PromptSet = get_prompts(prompt_version)

    def ask(self, leader_id: int, question: str) -> AgentResult:
        start = time.perf_counter()
        usage_before = (self.llm.usage.calls, self.llm.usage.input_tokens,
                        self.llm.usage.output_tokens)
        result = AgentResult(question=question, leader_id=leader_id, status="error", answer="")
        try:
            leader = get_leader(self.db_path, leader_id)
            db = ScopedDB.for_leader(self.db_path, leader)
            try:
                self._run(leader, db, question, result)
            finally:
                db.close()
        except Exception as e:  # the API must always get a result back
            result.status = "error"
            result.answer = "Something went wrong answering this question."
            result.steps.append(f"error: {type(e).__name__}: {e}")
        u = self.llm.usage
        result.llm_calls = u.calls - usage_before[0]
        result.input_tokens = u.input_tokens - usage_before[1]
        result.output_tokens = u.output_tokens - usage_before[2]
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        return result

    def _run(self, leader: Leader, db: ScopedDB, question: str, result: AgentResult) -> None:
        result.steps.append(f"scope: {leader.unit_name} ({db.size} people)")
        if db.size < MIN_GROUP_SIZE:  # nothing can ever be shown, so don't spend an LLM call
            result.status = "blocked"
            result.answer = (f"Your organisation has fewer than {MIN_GROUP_SIZE} people, "
                             "so no statistics can be shown.")
            result.steps.append("blocked: scope too small")
            return
        system = self.prompts.sql_system.format(schema=db.schema_text())
        user = self.prompts.sql_user.format(question=question)

        safe: SafeResult | None = None
        for attempt in range(1, MAX_SQL_ATTEMPTS + 1):
            raw = self.llm.complete(system, user)
            try:
                reply = parse_sql_reply(raw)
            except (ValueError, json.JSONDecodeError) as e:
                error, sql = f"bad reply format: {e}", raw
            else:
                if reply.get("refuse"):
                    result.status = "refused"
                    result.answer = str(reply["refuse"])
                    result.steps.append("llm refused")
                    return
                sql = str(reply.get("sql", ""))
                result.sql = sql
                try:
                    guarded = guard(sql)
                    safe = run_safe(db, guarded)
                    result.sql = guarded.original_sql
                    result.steps.append(f"sql ok on attempt {attempt}")
                    break
                except QueryBlocked as e:
                    result.status = "blocked"
                    result.answer = str(e)
                    result.steps.append("blocked by privacy rule")
                    return
                except SQLRejected as e:
                    error = f"guard: {e}"
                except Exception as e:  # DuckDB errors: bad column, bad GROUP BY, ...
                    error = f"database: {str(e).splitlines()[0]}"
            result.steps.append(f"sql attempt {attempt} rejected: {error}")
            user = self.prompts.sql_retry.format(question=question, previous_sql=sql, error=error)

        if safe is None:
            result.status = "error"
            result.answer = "I couldn't turn this question into a query I'm allowed to run."
            return

        result.hidden_groups = safe.hidden_groups
        result.rows = safe.rows.to_dict(orient="records")
        if safe.rows.empty:
            result.status = "no_data"
            result.answer = (
                "There is nothing I can show for this question: every matching group "
                "has fewer than 5 people." if safe.hidden_groups else "No one matched this question."
            )
            return

        base = self.prompts.answer_user.format(
            question=question,
            sql=result.sql,
            table=table_text(safe.rows),
            notes="\n".join(safe.notes),
        )
        user = base
        check: Verification | None = None
        for attempt in range(1, MAX_ANSWER_ATTEMPTS + 1):
            answer = self.llm.complete(self.prompts.answer_system, user).strip()
            check = verify_answer(answer, safe.rows, question=question, sql=result.sql or "")
            if check.passed:
                result.status = "answered"
                result.answer = answer
                result.steps.append(f"answer verified on attempt {attempt}")
                break
            result.steps.append(f"answer attempt {attempt} unsupported numbers: {check.unsupported}")
            user = self.prompts.answer_retry.format(base=base, unsupported=", ".join(check.unsupported))
        else:
            result.status = "fallback"
            result.answer = fallback_answer(safe.rows.drop(columns=[GROUP_SIZE_COL]),
                                            safe.hidden_groups)
            result.steps.append("used deterministic fallback answer")
        result.verification = check.__dict__ if check else None
