"""Run a guarded query and apply the small-group rules.

Three rules, all applied in code after the SQL runs:

1. Leader scope too small: if the leader's whole org has fewer than 5 people,
   nothing is reported at all.
2. Small groups: any result row built from fewer than 5 people is removed.
3. Differencing: two answers can be subtracted to expose a small group.
   - If a filter leaves out fewer than 5 people ("everyone except team 3"),
     the query is blocked, because "everyone" minus this answer = those people.
   - If the removed rows add up to fewer than 5 people, the next smallest rows
     are removed too, so the hidden rows can't be recovered from the total.
   This stops the obvious attacks. Determined attackers can still combine many
   different queries; the eval report says so, and a real system would add
   query auditing on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from workforce.config import MIN_GROUP_SIZE
from workforce.guard import GROUP_SIZE_COL, GuardedQuery
from workforce.scope import ScopedDB


class QueryBlocked(Exception):
    """The query ran into a privacy rule. The message is safe to show the leader."""


@dataclass
class SafeResult:
    rows: pd.DataFrame                # what may be shown; includes group_size
    hidden_groups: int = 0            # rows removed by the small-group rules
    notes: list[str] = field(default_factory=list)


def run_safe(db: ScopedDB, query: GuardedQuery, k: int = MIN_GROUP_SIZE) -> SafeResult:
    if db.size < k:
        raise QueryBlocked(
            f"Your organisation has fewer than {k} people, so no statistics can be shown."
        )

    if query.where_sql is not None:
        matched = db.count_where(query.where_sql)
        excluded = db.size - matched
        if 0 < excluded < k:
            raise QueryBlocked(
                f"This filter leaves out fewer than {k} people. Comparing it with your "
                "overall numbers would reveal information about them, so it can't be answered."
            )

    df = db.query(query.sql)
    if df.empty:
        return SafeResult(rows=df, notes=["The query matched nobody."])

    sizes = df[GROUP_SIZE_COL]
    hide = sizes < k
    notes = []

    # Complementary suppression: hidden rows must cover at least k people together
    if hide.any() and len(df) > 1:
        while 0 < sizes[hide].sum() < k and (~hide).any():
            smallest = sizes[~hide].idxmin()
            hide[smallest] = True

    hidden = int(hide.sum())
    if hidden:
        notes.append(
            f"{hidden} group(s) hidden because they cover fewer than {k} people "
            "(or would let a hidden group be worked out)."
        )
    return SafeResult(rows=df[~hide].reset_index(drop=True), hidden_groups=hidden, notes=notes)
