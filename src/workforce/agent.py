"""The question-answering agent.

    question
      -> code checks first: help questions, requests to pick people to cut
      -> LLM writes SQL, refuses, or splits a multi-part question (up to 3 parts)
      -> minimisation check + SQL guard            up to 2 attempts
      -> run in the leader's scoped database
      -> privacy rules (small groups, small cells, differencing, nobody matched)
      -> LLM writes the answer from the result     up to 2 attempts
      -> verify numbers, units, wording, no recommendations about people
      -> if still not clean: a plain, deterministic answer built from the table
    if the main prompt version can't produce an allowed query at all, the whole
    question is retried once with the fallback prompt version (v3 by default).

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
from workforce.policy import (
    COULD_NOT_ANSWER,
    DECISION_REFUSAL,
    answer_issues,
    help_text,
    is_help,
    is_people_decision,
    plain_refusal,
    unrequested_sensitive_columns,
)
from workforce.privacy import QueryBlocked, SafeResult, run_safe
from workforce.prompts import PromptSet, get_fallback_prompts, get_prompts
from workforce.scope import COLUMN_DOCS, Leader, ScopedDB, get_leader
from workforce.verify import Verification, verify_answer

MAX_SQL_ATTEMPTS = 2
MAX_ANSWER_ATTEMPTS = 2
MAX_PARTS = 3
OK_STATUSES = {"answered", "fallback"}


@dataclass
class AgentResult:
    question: str
    leader_id: int
    # answered | fallback | partial | refused | blocked | no_data | help | error
    status: str
    answer: str
    sql: str | None = None
    rows: list[dict] = field(default_factory=list)
    hidden_groups: int = 0
    masked_cells: int = 0
    assumption: str | None = None
    verification: dict | None = None
    steps: list[str] = field(default_factory=list)
    parts: list[AgentResult] = field(default_factory=list)
    prompt_version: str | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["parts"] = [p.to_dict() for p in self.parts]
        return d


def parse_sql_reply(text: str) -> dict:
    """Pull the JSON object out of the reply, tolerating code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("reply did not contain JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict) or not ({"sql", "refuse", "parts"} & data.keys()):
        raise ValueError("JSON must contain 'sql', 'parts' or 'refuse'")
    return data


def table_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "(no rows)"
    return df.to_csv(index=False)


def fallback_answer(rows: pd.DataFrame, hidden: int) -> str:
    """No LLM: just state the result, so every number is guaranteed correct."""
    parts = []
    for _, row in rows.head(10).iterrows():
        parts.append(", ".join(f"{k.replace('_', ' ')}: {'under 5' if pd.isna(v) else v}"
                               for k, v in row.items()))
    text = "Here is the result: " + "; ".join(parts) + "."
    if len(rows) > 10:
        text += f" ({len(rows) - 10} more rows in the table.)"
    if hidden:
        text += " Some groups were hidden for privacy."
    return text


def column_docs_for(sql: str) -> list[str]:
    """Descriptions of the columns a query uses, so scale numbers like '1 to 4' verify."""
    return [doc for col, doc in COLUMN_DOCS.items() if re.search(rf"\b{col}\b", sql or "")]


def _worth_fallback(result: AgentResult) -> bool:
    """Retry with the fallback prompts only when the model couldn't produce an allowed
    query. Never after an exception (API down, unknown leader)."""
    return (result.status == "error"
            and any("rejected" in s for s in result.steps)
            and not any(s.startswith("error:") for s in result.steps))


class WorkforceAgent:
    def __init__(self, db_path: Path, llm: LLM, prompt_version: str | None = None,
                 fallback_version: str | None = None):
        self.db_path = Path(db_path)
        self.llm = llm
        self.prompts: PromptSet = get_prompts(prompt_version)
        if fallback_version == "none":
            self.fallback_prompts = None
        elif fallback_version:
            self.fallback_prompts = get_prompts(fallback_version)
        else:
            self.fallback_prompts = get_fallback_prompts(self.prompts)

    def ask(self, leader_id: int, question: str) -> AgentResult:
        start = time.perf_counter()
        u0 = (self.llm.usage.calls, self.llm.usage.input_tokens, self.llm.usage.output_tokens)

        result = self._ask(leader_id, question, self.prompts, allow_parts=True)
        if self.fallback_prompts and _worth_fallback(result):
            first_steps = result.steps
            result = self._ask(leader_id, question, self.fallback_prompts, allow_parts=False)
            result.steps = first_steps + [f"retried with fallback prompts {self.fallback_prompts.version}"] \
                + result.steps

        u = self.llm.usage
        result.llm_calls = u.calls - u0[0]
        result.input_tokens = u.input_tokens - u0[1]
        result.output_tokens = u.output_tokens - u0[2]
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        return result

    def _ask(self, leader_id: int, question: str, prompts: PromptSet, allow_parts: bool) -> AgentResult:
        result = AgentResult(question=question, leader_id=leader_id, status="error", answer="",
                             prompt_version=prompts.version)
        try:
            leader = get_leader(self.db_path, leader_id)
            if is_help(question):
                result.status, result.answer = "help", help_text(leader.unit_name, leader.headcount)
                result.steps.append("help question: fixed answer, no LLM")
                return result
            if is_people_decision(question):
                result.status, result.answer = "refused", DECISION_REFUSAL
                result.steps.append("refused in code: recommendation about people's jobs")
                return result
            db = ScopedDB.for_leader(self.db_path, leader)
            try:
                self._run(leader, db, question, result, prompts, allow_parts)
            finally:
                db.close()
        except Exception as e:  # the API must always get a result back
            result.status = "error"
            result.answer = "Something went wrong answering this question."
            result.sql = None
            result.steps.append(f"error: {type(e).__name__}: {e}")
        return result

    def _run(self, leader: Leader, db: ScopedDB, question: str, result: AgentResult,
             prompts: PromptSet, allow_parts: bool) -> None:
        result.steps.append(f"scope: {leader.unit_name} ({db.size} people)")
        if db.size < MIN_GROUP_SIZE:  # nothing can ever be shown, so don't spend an LLM call
            result.status = "blocked"
            result.answer = (f"Your organisation has fewer than {MIN_GROUP_SIZE} people, "
                             "so no statistics can be shown.")
            result.steps.append("blocked: scope too small")
            return

        system = prompts.sql_system.format(schema=db.schema_text())
        user = prompts.sql_user.format(question=question)
        safe: SafeResult | None = None
        assumption = None
        for attempt in range(1, MAX_SQL_ATTEMPTS + 1):
            raw = self.llm.complete(system, user)
            sql = raw
            try:
                reply = parse_sql_reply(raw)
            except (ValueError, json.JSONDecodeError) as e:
                error = f"bad reply format: {e}"
            else:
                if reply.get("refuse"):
                    result.status = "refused"
                    result.answer = plain_refusal(reply["refuse"])
                    result.sql = None
                    result.steps.append("llm refused")
                    return
                parts = reply.get("parts")
                if parts and allow_parts:
                    parts = [str(p).strip() for p in parts if str(p).strip()][:MAX_PARTS]
                    if len(parts) >= 2:
                        self._run_parts(leader, db, parts, result, prompts)
                        return
                    error = "split into fewer than 2 parts; answer the question with one query"
                elif parts:
                    error = "this part must be answered with one query, not split further"
                else:
                    sql = str(reply.get("sql", ""))
                    assumption = (str(reply["assumption"]).strip()[:200] or None) \
                        if reply.get("assumption") else None
                    try:
                        extra = unrequested_sensitive_columns(question, sql)
                        if extra:
                            raise SQLRejected(f"the question does not ask about {', '.join(extra)}; "
                                              "select only the measures that were asked for")
                        guarded = guard(sql)
                        safe = run_safe(db, guarded)
                        result.sql = guarded.original_sql
                        result.steps.append(f"sql ok on attempt {attempt}")
                        break
                    except QueryBlocked as e:
                        result.status = "blocked"
                        result.answer = str(e)
                        result.sql = None
                        result.steps.append("blocked by privacy rule")
                        return
                    except SQLRejected as e:
                        error = f"guard: {e}"
                    except Exception as e:  # DuckDB errors: bad column, bad GROUP BY, ...
                        error = f"database: {str(e).splitlines()[0]}"
            result.steps.append(f"sql attempt {attempt} rejected: {error}")
            user = prompts.sql_retry.format(question=question, previous_sql=sql, error=error)

        if safe is None:
            result.status = "error"
            result.answer = COULD_NOT_ANSWER
            result.sql = None  # nothing ran, so there is no SQL to show
            return

        result.hidden_groups = safe.hidden_groups
        result.masked_cells = safe.masked_cells
        # via JSON so NaN becomes None and numpy numbers become plain Python numbers
        result.rows = json.loads(safe.rows.to_json(orient="records", double_precision=10))
        if safe.matched_nobody:
            result.status = "no_data"
            result.answer = (f"Nobody in your organisation ({leader.unit_name}, {db.size} people) matches "
                             "this question. You can only see people in your own part of the organisation.")
            return
        if safe.rows.empty:
            result.status = "no_data"
            result.answer = ("There is nothing I can show for this question: the matching groups are "
                             "too small to report on without identifying people.")
            return

        notes = "\n".join(safe.notes)
        if assumption:
            notes += f"\nThe question was vague; it was read as: {assumption}"
            result.assumption = assumption
        base = prompts.answer_user.format(question=question, sql=result.sql,
                                          table=table_text(safe.rows), notes=notes)
        user = base
        docs = column_docs_for(result.sql)
        check: Verification | None = None
        issues: list[str] = []
        for attempt in range(1, MAX_ANSWER_ATTEMPTS + 1):
            answer = self.llm.complete(prompts.answer_system, user).strip()
            check = verify_answer(answer, safe.rows, question=question, sql=result.sql or "",
                                  extra_texts=docs)
            issues = answer_issues(answer)
            if check.passed and not issues:
                result.status = "answered"
                result.answer = f"I took this to mean: {assumption}. {answer}" if assumption else answer
                result.steps.append(f"answer verified on attempt {attempt}")
                break
            problems = ([f"numbers not in the result: {', '.join(check.unsupported)}"]
                        if check.unsupported else []) + issues
            result.steps.append(f"answer attempt {attempt} problems: {'; '.join(problems)}")
            user = prompts.answer_retry.format(base=base, unsupported="; ".join(problems),
                                               problems="; ".join(problems))
        else:
            result.status = "fallback"
            result.answer = fallback_answer(safe.rows.drop(columns=[GROUP_SIZE_COL]), safe.hidden_groups)
            result.steps.append("used deterministic fallback answer")
        result.verification = {**check.__dict__, "issues": issues} if check else None

    def _run_parts(self, leader: Leader, db: ScopedDB, parts: list[str], result: AgentResult,
                   prompts: PromptSet) -> None:
        """A multi-part question: answer each part on its own, through every check."""
        result.steps.append(f"split into {len(parts)} parts")
        for text in parts:
            sub = AgentResult(question=text, leader_id=leader.leader_id, status="error", answer="",
                              prompt_version=prompts.version)
            if is_people_decision(text):
                sub.status, sub.answer = "refused", DECISION_REFUSAL
            else:
                try:
                    self._run(leader, db, text, sub, prompts, allow_parts=False)
                except Exception as e:
                    sub.status, sub.answer = "error", "Something went wrong answering this part."
                    sub.steps.append(f"error: {type(e).__name__}: {e}")
            result.parts.append(sub)
            result.steps += [f"[{text}] {s}" for s in sub.steps]

        statuses = [p.status for p in result.parts]
        if all(s in OK_STATUSES for s in statuses):
            result.status = "answered"
        elif any(s in OK_STATUSES for s in statuses):
            result.status = "partial"
        else:
            result.status = statuses[0] if len(set(statuses)) == 1 else "partial"
        result.answer = " ".join(p.answer for p in result.parts)
        result.hidden_groups = sum(p.hidden_groups for p in result.parts)
        result.masked_cells = sum(p.masked_cells for p in result.parts)
