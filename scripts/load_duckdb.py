"""Day 1, step 2: load data/processed/*.csv into data/workforce.duckdb and run sanity queries.

Usage:
    python scripts/load_duckdb.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from workforce.config import DUCKDB_PATH, PROCESSED_DIR
from workforce.db import load_duckdb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--db", type=Path, default=DUCKDB_PATH)
    args = parser.parse_args()

    counts = load_duckdb(args.processed, args.db)
    for table, n in counts.items():
        print(f"{table:12s} {n:6d} rows")

    con = duckdb.connect(str(args.db), read_only=True)
    try:
        total = counts["employees"]
        root_hc = con.execute(
            "SELECT h.headcount FROM unit_headcount h "
            "JOIN org_units u USING (org_unit_id) WHERE u.parent_unit_id IS NULL"
        ).fetchone()[0]
        print(f"Company headcount from the closure table: {root_hc} (should equal {total})")

        print("\nDepartments:")
        rows = con.execute(
            "SELECT u.name, h.headcount, "
            "ROUND(100.0 * AVG(CASE WHEN e.attrition THEN 1 ELSE 0 END), 1) AS attrition_pct "
            "FROM org_units u "
            "JOIN unit_headcount h USING (org_unit_id) "
            "JOIN org_closure c ON c.ancestor_unit_id = u.org_unit_id "
            "JOIN employees e ON e.org_unit_id = c.descendant_unit_id "
            "WHERE u.unit_type = 'department' "
            "GROUP BY u.name, h.headcount ORDER BY h.headcount DESC"
        ).fetchall()
        for name, hc, pct in rows:
            print(f"  {name:25s} headcount {hc:5d}  attrition {pct}%")

        print("\nSample leaders (one per level):")
        rows = con.execute(
            "SELECT leader_id, unit_name, unit_type, headcount FROM leaders "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY level ORDER BY headcount DESC) = 1 "
            "ORDER BY level"
        ).fetchall()
        for leader_id, unit, utype, hc in rows:
            print(f"  employee {leader_id:5d} leads {unit} ({utype}, {hc} people)")
    finally:
        con.close()

    ok = root_hc == total
    print("\nDuckDB load OK" if ok else "\nDuckDB load FAILED: headcounts do not match")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
