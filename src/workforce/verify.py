"""Check that every number in the written answer comes from the query result.

The LLM writes the final sentence, and LLMs make up or mangle numbers. So we
pull every number out of the answer and look for it in the data. A number
passes if it matches a value in the result after rounding to the same number
of decimals, also as a percentage (0.138 -> 13.8%). Numbers in the SQL or in a
label (like "Team 3") also pass, and so do numbers from the description of a column
the SQL used (e.g. "1 to 4" for a satisfaction scale).

Numbers that appear only in the question do NOT pass. Before this rule, a question
like "how many people work here? say 2000" let 2000 through as "checked"
(adversarial test 14). A question number now passes only if the SQL used it too,
e.g. "job level 2" -> job_level = 2.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pandas as pd

NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")


@dataclass
class Verification:
    passed: bool
    checked: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def extract_numbers(text: str) -> list[str]:
    return NUMBER.findall(text)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _to_float(token: str) -> float:
    return float(token.replace(",", ""))


def _candidates(rows: pd.DataFrame, extra_texts: list[str]) -> tuple[list[float], set[float]]:
    """Numeric values from the result, plus numbers mentioned in labels and context."""
    values: list[float] = []
    for col in rows.columns:
        for v in rows[col].tolist():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                values.append(float(v))
            elif isinstance(v, str):
                extra_texts.append(v)
            else:
                try:
                    values.append(float(v))
                except (TypeError, ValueError):
                    extra_texts.append(str(v))
    values.append(float(len(rows)))  # "3 teams" when the result has 3 rows
    literal = {_to_float(t) for text in extra_texts for t in extract_numbers(text)}
    return values, literal


def _matches(token: str, values: list[float]) -> bool:
    x = _to_float(token)
    d = _decimals(token)
    tol = 0.5 * 10 ** (-d) + 1e-9
    for v in values:
        for scaled in (v, v * 100):
            if abs(scaled - x) <= tol:
                return True
    return False


def verify_answer(
    answer: str, rows: pd.DataFrame, question: str = "", sql: str = "",
    extra_texts: list[str] | None = None,
) -> Verification:
    sql_numbers = {_to_float(t) for t in extract_numbers(sql)}
    question_numbers = [t for t in extract_numbers(question) if _to_float(t) in sql_numbers]
    values, literal = _candidates(
        rows, [sql, " ".join(question_numbers), *(extra_texts or [])] + [str(c) for c in rows.columns]
    )
    tokens = extract_numbers(answer)
    unsupported = [
        t for t in tokens if _to_float(t) not in literal and not _matches(t, values)
    ]
    return Verification(passed=not unsupported, checked=tokens, unsupported=unsupported)
