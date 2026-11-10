"""Run a guarded query and apply the small-group rules.

Three rules, all applied in code after the SQL runs:

1. Leader scope too small: if the leader's whole org has fewer than 5 people,
   nothing is reported at all.
2. Small groups: any result row built from fewer than 5 people is removed.
3. Small cells: a column that counts a subgroup inside a row (e.g. a pivot with
   female_count per job level) can show 1-4 people even when the row as a whole
   is large. Those cells are masked, and so are the other count cells in the same
   row, so the masked one can't be worked out by subtraction. (Found by the
   unseen eval, case k05.)
4. Differencing: two answers can be subtracted to expose a small group.
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
    masked_cells: int = 0             # count cells blanked by the small-cell rule
    matched_nobody: bool = False      # no one in scope matched (not the same as "hidden")


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
    # A COUNT with no GROUP BY returns one row even when nobody matched. That row has
    # group_size 0: it is "nobody", not "a small group" (adversarial test 20).
    df = df[df[GROUP_SIZE_COL] > 0].reset_index(drop=True)
    if df.empty:
        return SafeResult(rows=df, notes=["Nobody matched."], matched_nobody=True)

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
            f"{hidden} group(s) hidden for privacy: small groups, plus any group that would let "
            "a small one be worked out. Say that some groups were hidden for privacy; do not say "
            "how many people they contain."
        )
    shown = df[~hide].reset_index(drop=True)
    shown, masked = mask_small_cells(shown, query.count_columns or [], k)
    if masked:
        notes.append(f"{masked} count(s) under {k} people blanked out.")
    return SafeResult(rows=shown, hidden_groups=hidden, notes=notes, masked_cells=masked)


def mask_small_cells(df: pd.DataFrame, count_columns: list[str], k: int) -> tuple[pd.DataFrame, int]:
    cols = [c for c in count_columns if c in df.columns]
    if not cols or df.empty:
        return df, 0
    df = df.copy().astype({c: "object" for c in cols})
    masked = 0
    for i in df.index:
        values = [df.at[i, c] for c in cols]
        small = any(v is not None and not pd.isna(v) and 0 < float(v) < k for v in values)
        if small:
            for c in cols:
                if df.at[i, c] is not None and not pd.isna(df.at[i, c]):
                    df.at[i, c] = None
                    masked += 1
    return df, masked
