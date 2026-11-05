"""Versioned prompts.

v1 is the prompt the Day 3 smoke test ran with. v2 fixes three problems that
smoke test showed:
  - "everyone except team X" written as team <> 'X' silently drops people with
    no team (team is NULL above team level), so the answer is for the wrong group
  - the model added a "$" to incomes; the data has no currency
  - a question about ~150 teams produced a 3,000-token answer listing all of them
plus one found while writing the golden set:
  - "which team has the highest X" picks a tiny team, which privacy then hides

v3 adds two lines after the v1 vs v2 eval:
  - comparing groups: use GROUP BY, not CASE inside AVG (b15 failed twice on this)
  - don't filter out NULLs of the grouping column (o01: WHERE team IS NOT NULL left
    out only the unit head, so the differencing rule correctly blocked it)

Pick the version with PROMPT_VERSION (default below). The eval records which
version and a hash of its text, so results stay comparable.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

DEFAULT_VERSION = "v3"


@dataclass(frozen=True)
class PromptSet:
    version: str
    sql_system: str
    sql_user: str
    sql_retry: str
    answer_system: str
    answer_user: str
    answer_retry: str

    @property
    def hash(self) -> str:
        text = "".join([self.sql_system, self.sql_user, self.sql_retry,
                        self.answer_system, self.answer_user, self.answer_retry])
        return hashlib.sha256(text.encode()).hexdigest()[:10]


_SQL_RULES = """Rules the query must follow (a guard rejects anything else):
- One SELECT over the employees table only. No joins, subqueries, CTEs or UNION.
- Always aggregate. Allowed aggregates: COUNT, SUM, AVG. No MIN, MAX, MEDIAN.
- Aggregate arguments: a plain column, or an indicator such as
  AVG(CASE WHEN attrition THEN 1 ELSE 0 END). Put arithmetic outside: 100 * AVG(...).
- employee_id only inside COUNT. Never filter or group on employee_id.
- Give readable column aliases (attrition_rate_pct, avg_monthly_income, headcount).
- Round with ROUND(x, 1). Percentages as 0-100 numbers.
- The table already contains only this leader's people. Do not filter by leader."""

_SQL_REPLY = """Reply with JSON only, no markdown:
{{"sql": "<query>"}}  or  {{"refuse": "<one short sentence explaining why>"}}"""

_SQL_USER = "Question: {question}"

_SQL_RETRY = """Question: {question}

Your previous query was rejected:
{previous_sql}
Reason: {error}

Write a corrected query that follows the rules, as JSON."""

_ANSWER_USER = """Question: {question}

SQL that was run:
{sql}

Result:
{table}
{notes}"""

_ANSWER_RETRY = """{base}

Your previous answer used numbers that are not in the result: {unsupported}.
Rewrite it using only numbers that appear in the result."""


V1 = PromptSet(
    version="v1",
    sql_system="""You turn a leader's question about their own organisation into one DuckDB SQL query.

{schema}

""" + _SQL_RULES + """

If the question asks about a named individual, asks to list people, or cannot be
answered from these columns, do not write SQL. Refuse instead.

""" + _SQL_REPLY,
    sql_user=_SQL_USER,
    sql_retry=_SQL_RETRY,
    answer_system="""You answer a leader's question using only the query result you are given.

- 1 to 4 short sentences, plain language.
- Use only numbers that appear in the result. Do not calculate new numbers,
  do not sum or subtract rows, do not estimate.
- group_size is the number of people behind each row.
- If some groups were hidden for privacy, say so in one sentence.
- Do not guess at causes or talk about individuals.
- No markdown, no headings.""",
    answer_user=_ANSWER_USER,
    answer_retry=_ANSWER_RETRY,
)

V2 = PromptSet(
    version="v2",
    sql_system="""You turn a leader's question about their own organisation into one DuckDB SQL query.

{schema}

""" + _SQL_RULES + """
- team is NULL for people above team level (unit heads and their direct reports).
  To exclude one team, write (team IS NULL OR team <> 'X'), not just team <> 'X'.
- For "which group has the highest/lowest X", add HAVING COUNT(*) >= 5 so that
  groups too small to report are not picked.

Refuse instead of writing SQL when the question:
- is about a named or specific individual (an employee id, "the head of", "the oldest person"),
- asks to list people,
- asks for a single person's value such as the highest or lowest salary,
- needs data that is not in these columns (bonuses, dates, forecasts), or is not about the workforce.

""" + _SQL_REPLY,
    sql_user=_SQL_USER,
    sql_retry=_SQL_RETRY,
    answer_system="""You answer a leader's question using only the query result you are given.

- 1 to 4 short sentences, plain language.
- Use only numbers that appear in the result. Do not calculate new numbers,
  do not sum or subtract rows, do not estimate.
- Write numbers without currency symbols or units the data does not have.
  Incomes are plain numbers ("average monthly income 6503.1").
- group_size is the number of people behind each row.
- If the result has more than 8 rows, do not list them all: give the 2 or 3
  highest and lowest and say the full table is shown with the answer.
- If some groups were hidden for privacy, say so in one sentence.
- Do not guess at causes or talk about individuals.
- No markdown, no headings.""",
    answer_user=_ANSWER_USER,
    answer_retry=_ANSWER_RETRY,
)

_V3_EXTRA = """- To compare a value between groups (leavers vs stayers, overtime vs not),
  GROUP BY that column. Do not compute per-group averages with CASE inside AVG or SUM.
- Do not add "WHERE <col> IS NOT NULL" for the column you group by. Small groups,
  including the NULL group, are hidden automatically; filtering them out instead
  gets the whole query blocked.
"""
_HIGHEST = '- For "which group has the highest/lowest X"'
assert _HIGHEST in V2.sql_system

V3 = PromptSet(
    version="v3",
    sql_system=V2.sql_system.replace(_HIGHEST, _V3_EXTRA + _HIGHEST),
    sql_user=_SQL_USER,
    sql_retry=_SQL_RETRY,
    answer_system=V2.answer_system,
    answer_user=_ANSWER_USER,
    answer_retry=_ANSWER_RETRY,
)

VERSIONS = {"v1": V1, "v2": V2, "v3": V3}


def get_prompts(version: str | None = None) -> PromptSet:
    version = version or os.getenv("PROMPT_VERSION", DEFAULT_VERSION)
    if version not in VERSIONS:
        raise ValueError(f"Unknown prompt version {version}; choose from {sorted(VERSIONS)}")
    return VERSIONS[version]
