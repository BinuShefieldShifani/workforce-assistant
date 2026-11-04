"""Load the processed CSVs into DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb

from workforce.config import TABLES

# Headcount per unit, including everything below it. Day 2's suppression rule uses this.
UNIT_HEADCOUNT_VIEW = """
CREATE OR REPLACE VIEW unit_headcount AS
SELECT c.ancestor_unit_id AS org_unit_id, COUNT(e.employee_id) AS headcount
FROM org_closure c
JOIN employees e ON e.org_unit_id = c.descendant_unit_id
GROUP BY c.ancestor_unit_id
"""

# The people who can log in later: everyone who heads a unit
LEADERS_VIEW = """
CREATE OR REPLACE VIEW leaders AS
SELECT u.head_employee_id AS leader_id, u.org_unit_id, u.name AS unit_name,
       u.unit_type, u.level, h.headcount
FROM org_units u
JOIN unit_headcount h USING (org_unit_id)
"""


def load_duckdb(processed_dir: Path, db_path: Path) -> dict[str, int]:
    """(Re)create the tables and views. Returns row counts per table."""
    processed_dir, db_path = Path(processed_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        counts = {}
        for table in TABLES:
            csv = processed_dir / f"{table}.csv"
            if not csv.exists():
                raise FileNotFoundError(f"{csv} not found. Run scripts/build_org.py first.")
            con.execute(
                f"CREATE OR REPLACE TABLE {table} AS "
                "SELECT * FROM read_csv(?, header = true, auto_detect = true)",
                [str(csv)],
            )
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.execute(UNIT_HEADCOUNT_VIEW)
        con.execute(LEADERS_VIEW)
        return counts
    finally:
        con.close()
