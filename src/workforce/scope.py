"""Access scoping: a leader only ever sees their own part of the org.

For each question we build a fresh in-memory DuckDB that contains *only* the
leader's people, in one table called `employees`. The SQL the LLM writes runs
there. So even if a query slipped past the SQL guard, there is nothing outside
the leader's scope for it to read. File access and Python variable lookups are
switched off on that connection, and the settings are locked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd


class AccessDenied(Exception):
    """The person asking is not a leader of any unit."""


@dataclass(frozen=True)
class Leader:
    leader_id: int
    org_unit_id: str
    unit_name: str
    unit_type: str
    headcount: int


# Column descriptions shown to the LLM. Codes follow the IBM dataset's documentation.
COLUMN_DOCS = {
    "employee_id": "unique id of the person",
    "age": "age in years",
    "attrition": "TRUE if the person has left the company",
    "business_travel": "'Non-Travel', 'Travel_Rarely' or 'Travel_Frequently'",
    "department": "'Research & Development', 'Sales' or 'Human Resources'",
    "distance_from_home": "commute distance, in km",
    "education": "1 Below College, 2 College, 3 Bachelor, 4 Master, 5 Doctor",
    "education_field": "field of study, e.g. 'Life Sciences', 'Medical', 'Marketing'",
    "environment_satisfaction": "1 Low, 2 Medium, 3 High, 4 Very High",
    "gender": "'Male' or 'Female'",
    "job_involvement": "1 Low, 2 Medium, 3 High, 4 Very High",
    "job_level": "1 (junior) to 5 (most senior)",
    "job_role": "job title, e.g. 'Sales Executive', 'Research Scientist'",
    "job_satisfaction": "1 Low, 2 Medium, 3 High, 4 Very High",
    "marital_status": "'Single', 'Married' or 'Divorced'",
    "monthly_income": "monthly salary",
    "num_companies_worked": "number of previous employers",
    "over_time": "TRUE if the person works overtime",
    "percent_salary_hike": "last salary increase, in percent",
    "performance_rating": "3 Excellent, 4 Outstanding",
    "relationship_satisfaction": "1 Low, 2 Medium, 3 High, 4 Very High",
    "stock_option_level": "0 to 3",
    "total_working_years": "total years of work experience",
    "training_times_last_year": "number of trainings last year",
    "work_life_balance": "1 Bad, 2 Good, 3 Better, 4 Best",
    "years_at_company": "years at this company",
    "years_in_current_role": "years in current role",
    "years_since_last_promotion": "years since last promotion",
    "manager_id": "employee_id of the person's manager",
    "org_unit": "name of the unit the person sits in",
    "team": "team name, e.g. 'Sales / Sales Executive / Team 3' (NULL for people above team level)",
    "is_manager": "TRUE if the person heads a unit",
}


def get_leader(db_path: Path, leader_id: int) -> Leader:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT leader_id, org_unit_id, unit_name, unit_type, headcount "
            "FROM leaders WHERE leader_id = ?",
            [int(leader_id)],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise AccessDenied(f"Employee {leader_id} does not lead any unit")
    return Leader(int(row[0]), row[1], row[2], row[3], int(row[4]))


def list_leaders(db_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "SELECT leader_id, unit_name, unit_type, headcount FROM leaders "
            "ORDER BY level, headcount DESC"
        ).df()
    finally:
        con.close()


def scoped_employees(db_path: Path, leader: Leader) -> pd.DataFrame:
    """Everyone in the leader's unit and all units below it, with org columns added."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            SELECT e.* EXCLUDE (org_unit_id),
                   u.name AS org_unit,
                   CASE WHEN u.unit_type = 'team' THEN u.name END AS team,
                   e.employee_id IN (SELECT head_employee_id FROM org_units) AS is_manager
            FROM employees e
            JOIN org_closure c ON c.descendant_unit_id = e.org_unit_id
            JOIN org_units u ON u.org_unit_id = e.org_unit_id
            WHERE c.ancestor_unit_id = ?
            ORDER BY e.employee_id
            """,
            [leader.org_unit_id],
        ).df()
    finally:
        con.close()
    return df


class ScopedDB:
    """An isolated in-memory database holding only one leader's people."""

    def __init__(self, employees: pd.DataFrame):
        self.size = len(employees)
        self.columns = list(employees.columns)
        self.con = duckdb.connect(":memory:")
        self.con.register("_scope_src", employees)
        self.con.execute("CREATE TABLE employees AS SELECT * FROM _scope_src")
        self.con.unregister("_scope_src")
        # Lock the connection down: no files, no Python variables, no changing settings back
        self.con.execute("SET python_enable_replacements = false")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    @classmethod
    def for_leader(cls, db_path: Path, leader: Leader) -> ScopedDB:
        return cls(scoped_employees(db_path, leader))

    def query(self, sql: str) -> pd.DataFrame:
        return self.con.execute(sql).df()

    def count_where(self, where_sql: str) -> int:
        sql = f"SELECT COUNT(*) FROM employees WHERE {where_sql}"
        return int(self.con.execute(sql).fetchone()[0])

    def schema_text(self) -> str:
        types = dict(self.con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'employees'"
        ).fetchall())
        lines = [
            f"- {col} ({types.get(col, '?')}): {COLUMN_DOCS.get(col, '')}" for col in self.columns
        ]
        return "Table employees, one row per person:\n" + "\n".join(lines)

    def close(self) -> None:
        self.con.close()
