"""SQL guard: only allow simple aggregate questions over the `employees` table.

Rules, and why:
- One SELECT statement. No INSERT/UPDATE/DROP, no UNION, no CTEs, no subqueries,
  no joins. Keeping the grammar small makes the privacy checks reliable.
- Only the `employees` table. It is the only table in the scoped database anyway.
- Must aggregate. The assistant reports on groups, never on individuals.
- Only COUNT, SUM and AVG. MIN, MAX, MEDIAN and similar can return one
  specific person's value even from a large group.
- No window functions, no SELECT DISTINCT, no FILTER clauses.
- Inside SUM/AVG/COUNT the argument must be a plain column (optionally CAST) or
  an indicator: CASE/IF whose results are only literals or NULL. This blocks
  tricks like SUM(CASE WHEN employee_id = 42 THEN monthly_income END), which
  would pull one person's salary out of a large group.
- employee_id may only appear inside COUNT, and neither employee_id nor
  manager_id may appear in a condition inside an aggregate.

If the query passes, we add COUNT(*) AS group_size to it, so every result row
carries the number of people behind it. The privacy layer needs that number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from workforce.config import MAX_RESULT_ROWS
from workforce.policy import UNIT_TOKENS

ALLOWED_TABLES = {"employees"}
ALLOWED_AGGREGATES = (exp.Count, exp.Sum, exp.Avg)
GROUP_SIZE_COL = "group_size"


class SQLRejected(Exception):
    """The query breaks one of the guard's rules. The message says which."""


@dataclass
class GuardedQuery:
    sql: str                   # the query that will run, with group_size added
    original_sql: str          # what the LLM wrote
    where_sql: str | None      # the WHERE condition, used by the complement check
    metric_columns: list[str]  # output columns that hold aggregates
    count_columns: list[str] = None  # metric columns that count a subgroup of people


IDENTIFYING_COLUMNS = {"employee_id", "manager_id"}


def _is_null_or_literal(node: exp.Expression | None) -> bool:
    return node is None or isinstance(node, (exp.Literal, exp.Null, exp.Boolean)) or (
        isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal)
    )


def _strip(node: exp.Expression) -> exp.Expression:
    """Look through CAST, DISTINCT and brackets."""
    while isinstance(node, (exp.Cast, exp.TryCast, exp.Paren, exp.Distinct)):
        node = node.expressions[0] if isinstance(node, exp.Distinct) else node.this
    return node


def _check_aggregate_argument(agg: exp.AggFunc) -> None:
    arg = agg.this
    if arg is None:
        raise SQLRejected(f"{agg.key.upper()} needs an argument")
    arg = _strip(arg)
    if isinstance(arg, exp.Star) and isinstance(agg, exp.Count):
        return
    if isinstance(arg, exp.Column):
        return
    if isinstance(arg, (exp.Case, exp.If)):
        branches = [i.args.get("true") for i in arg.find_all(exp.If)]
        branches.append(arg.args.get("default") if isinstance(arg, exp.Case) else arg.args.get("false"))
        if not all(_is_null_or_literal(b) for b in branches):
            raise SQLRejected(
                "CASE/IF inside an aggregate may only return numbers or NULL "
                "(an indicator like CASE WHEN attrition THEN 1 ELSE 0 END). "
                "To compare a value between groups (e.g. leavers vs stayers), "
                "GROUP BY that column instead: SELECT attrition, AVG(x) ... GROUP BY attrition"
            )
        for col in arg.find_all(exp.Column):
            if col.name.lower() in IDENTIFYING_COLUMNS:
                raise SQLRejected(f"{col.name} cannot be used in a condition inside an aggregate")
        return
    raise SQLRejected(
        f"the argument of {agg.key.upper()} must be a column or a CASE indicator; "
        "put arithmetic outside the aggregate, e.g. 100 * AVG(...)"
    )


def _is_count_of_people(projection: exp.Expression) -> bool:
    """True for a column that counts a subgroup, e.g. SUM(CASE WHEN gender = 'Female' THEN 1 END)
    or COUNT(team). Rates like 100 * AVG(...) and COUNT(*) (= group_size) are not."""
    node = projection.this if isinstance(projection, exp.Alias) else projection
    while isinstance(node, (exp.Round, exp.Cast, exp.TryCast, exp.Paren)):
        node = node.this
    if isinstance(node, exp.Count):
        arg = node.this
        return arg is not None and not isinstance(arg, (exp.Star, exp.Distinct))
    if isinstance(node, exp.Sum):
        arg = _strip(node.this)
        return isinstance(arg, (exp.Case, exp.If, exp.Cast)) or (
            isinstance(node.this, (exp.Cast, exp.TryCast)) and isinstance(arg, exp.Column)
        )
    return False


def _directly_counted(col: exp.Column) -> bool:
    parent = col.parent
    if isinstance(parent, exp.Distinct):
        parent = parent.parent
    return isinstance(parent, exp.Count)


def guard(sql: str, max_rows: int = MAX_RESULT_ROWS) -> GuardedQuery:
    sql = sql.strip().rstrip(";")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLRejected(f"could not parse SQL: {str(e).splitlines()[0]}") from e

    if len(statements) != 1:
        raise SQLRejected("exactly one SQL statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Select):
        raise SQLRejected("only SELECT queries are allowed")
    for node_type, reason in [
        (exp.SetOperation, "UNION/INTERSECT/EXCEPT is not allowed"),
        (exp.With, "CTEs (WITH ...) are not allowed"),
        (exp.Subquery, "subqueries are not allowed"),
        (exp.Join, "joins are not allowed; everything is in the employees table"),
        (exp.Window, "window functions are not allowed"),
        (exp.Lateral, "LATERAL is not allowed"),
        (exp.Unnest, "UNNEST is not allowed"),
    ]:
        if tree.find(node_type):
            raise SQLRejected(reason)
    if any(isinstance(s, exp.Select) for s in tree.find_all(exp.Select) if s is not tree):
        raise SQLRejected("nested SELECTs are not allowed")
    if tree.args.get("distinct"):
        raise SQLRejected("SELECT DISTINCT is not allowed")

    tables = list(tree.find_all(exp.Table))
    names = {t.name.lower() for t in tables}
    if len(tables) != 1 or names != ALLOWED_TABLES or tables[0].args.get("db"):
        raise SQLRejected("the query must read from the employees table only")

    if tree.find(exp.Filter):
        raise SQLRejected("FILTER clauses are not allowed; use a CASE indicator instead")

    for agg in tree.find_all(exp.AggFunc):
        if not isinstance(agg, ALLOWED_AGGREGATES):
            raise SQLRejected(
                f"aggregate {agg.key.upper()} is not allowed; use COUNT, SUM or AVG"
            )
        _check_aggregate_argument(agg)

    for col in tree.find_all(exp.Column):
        if col.name.lower() == "employee_id" and not _directly_counted(col):
            raise SQLRejected("employee_id can only be used as COUNT(employee_id)")

    projections = tree.expressions
    if any(isinstance(p, exp.Star) for p in projections):
        raise SQLRejected("SELECT * is not allowed; ask for counts or averages")
    metric_flags = [p.find(exp.AggFunc) is not None for p in projections]
    if not any(metric_flags):
        raise SQLRejected(
            "the query must aggregate (COUNT, SUM or AVG); individual-level results are not allowed"
        )
    names_out = [p.alias_or_name for p in projections]
    for name in names_out:
        tokens = set(re.split(r"[_\W]+", name.lower()))
        if tokens & UNIT_TOKENS:
            raise SQLRejected(
                f"column name '{name}' names a currency or unit; the data has none, "
                "so name columns without units (e.g. avg_monthly_income)"
            )
    if GROUP_SIZE_COL in names_out:
        raise SQLRejected(f"'{GROUP_SIZE_COL}' is a reserved column name")
    metric_columns = [n for n, m in zip(names_out, metric_flags, strict=True) if m]
    count_columns = [n for n, p in zip(names_out, projections, strict=True) if _is_count_of_people(p)]

    where = tree.args.get("where")
    where_sql = where.this.sql(dialect="duckdb") if where is not None else None

    guarded = tree.copy()
    guarded = guarded.select(
        exp.alias_(exp.Count(this=exp.Star()), GROUP_SIZE_COL), append=True, copy=False
    )
    limit = guarded.args.get("limit")
    if limit is None:
        guarded = guarded.limit(max_rows, copy=False)
    else:
        try:
            n = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError) as e:
            raise SQLRejected("LIMIT must be a plain number") from e
        if n > max_rows:
            guarded = guarded.limit(max_rows, copy=False)

    return GuardedQuery(
        sql=guarded.sql(dialect="duckdb"),
        original_sql=sql,
        where_sql=where_sql,
        metric_columns=metric_columns,
        count_columns=count_columns,
    )
