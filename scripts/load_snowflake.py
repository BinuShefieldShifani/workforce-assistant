"""Day 1, step 3 (optional): load the same three tables into Snowflake.

Needs: pip install -e ".[snowflake]" and a filled-in .env (copy .env.example).

Usage:
    python scripts/load_snowflake.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

from workforce.config import PROCESSED_DIR, ROOT, TABLES

UNIT_HEADCOUNT_VIEW = """
CREATE OR REPLACE VIEW UNIT_HEADCOUNT AS
SELECT c.ANCESTOR_UNIT_ID AS ORG_UNIT_ID, COUNT(e.EMPLOYEE_ID) AS HEADCOUNT
FROM ORG_CLOSURE c
JOIN EMPLOYEES e ON e.ORG_UNIT_ID = c.DESCENDANT_UNIT_ID
GROUP BY c.ANCESTOR_UNIT_ID
"""


def connection_params() -> dict:
    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {missing}. Copy .env.example to .env first.")
    params = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        "authenticator": os.getenv("SNOWFLAKE_AUTHENTICATOR"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "private_key_file": os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"),
    }
    return {k: v for k, v in params.items() if v}


def read_processed(name: str) -> pd.DataFrame:
    path = PROCESSED_DIR / f"{name}.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run scripts/build_org.py first.")
    dtypes = {"manager_id": "Int64"} if name == "employees" else None
    df = pd.read_csv(path, dtype=dtypes)
    df.columns = [c.upper() for c in df.columns]  # unquoted Snowflake identifiers
    return df


def main() -> int:
    try:
        import snowflake.connector
        from dotenv import load_dotenv
        from snowflake.connector.pandas_tools import write_pandas
    except ImportError:
        print('Snowflake extras not installed. Run: pip install -e ".[snowflake]"')
        return 1

    load_dotenv(ROOT / ".env")
    database = os.getenv("SNOWFLAKE_DATABASE", "WORKFORCE_DEMO")
    schema = os.getenv("SNOWFLAKE_SCHEMA", "HR")

    frames = {t: read_processed(t) for t in TABLES}

    print("Connecting to Snowflake (a browser tab may open for login)...")
    conn = snowflake.connector.connect(**connection_params())
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
        cur.execute(f"USE SCHEMA {database}.{schema}")

        ok = True
        for table, df in frames.items():
            success, _, nrows, _ = write_pandas(
                conn,
                df,
                table_name=table.upper(),
                database=database,
                schema=schema,
                auto_create_table=True,
                overwrite=True,
                quote_identifiers=False,
            )
            count = cur.execute(f"SELECT COUNT(*) FROM {table.upper()}").fetchone()[0]
            match = success and count == len(df)
            ok = ok and match
            print(f"{table.upper():12s} uploaded {nrows}, in table {count}, "
                  f"expected {len(df)}  {'OK' if match else 'MISMATCH'}")

        cur.execute(UNIT_HEADCOUNT_VIEW)
        root_hc = cur.execute(
            "SELECT h.HEADCOUNT FROM UNIT_HEADCOUNT h "
            "JOIN ORG_UNITS u ON u.ORG_UNIT_ID = h.ORG_UNIT_ID "
            "WHERE u.PARENT_UNIT_ID IS NULL"
        ).fetchone()[0]
        total = len(frames["employees"])
        print(f"Company headcount in Snowflake: {root_hc} (should equal {total})")
        ok = ok and root_hc == total
    finally:
        conn.close()

    print(f"\nSnowflake load OK: {database}.{schema}" if ok else "\nSnowflake load FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
