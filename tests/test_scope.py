import duckdb
import pytest
from conftest import leader_where

from workforce.scope import AccessDenied, ScopedDB, get_leader, scoped_employees


def test_scope_matches_headcount(db_path):
    lid = leader_where(db_path, "unit_type = 'department'")
    leader = get_leader(db_path, lid)
    df = scoped_employees(db_path, leader)
    assert len(df) == leader.headcount
    assert lid in set(df["employee_id"])


def test_scoped_db_holds_only_the_leaders_people(db_path):
    lid = leader_where(db_path, "unit_type = 'function'")
    leader = get_leader(db_path, lid)
    db = ScopedDB.for_leader(db_path, leader)
    try:
        assert db.query("SELECT COUNT(*) AS n FROM employees")["n"][0] == leader.headcount
        tables = db.query("SELECT table_name FROM information_schema.tables")["table_name"].tolist()
        assert tables == ["employees"]
    finally:
        db.close()


def test_non_leader_is_denied(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    non_leader = con.execute(
        "SELECT employee_id FROM employees WHERE employee_id NOT IN "
        "(SELECT head_employee_id FROM org_units) LIMIT 1"
    ).fetchone()[0]
    con.close()
    with pytest.raises(AccessDenied):
        get_leader(db_path, non_leader)


def test_scoped_db_cannot_read_files_or_python_variables(db_path, tmp_path):
    leader = get_leader(db_path, leader_where(db_path, "unit_type = 'company'"))
    db = ScopedDB.for_leader(db_path, leader)
    secret = db.query("SELECT 1 AS x")  # a local DataFrame DuckDB could otherwise scan  # noqa: F841
    try:
        with pytest.raises(duckdb.Error):
            db.query(f"SELECT * FROM read_csv('{tmp_path / 'processed' / 'employees.csv'}')")
        with pytest.raises(duckdb.Error):
            db.query("SELECT * FROM secret")
        with pytest.raises(duckdb.Error):
            db.query("SET enable_external_access = true")
    finally:
        db.close()


def test_schema_text_lists_columns(db_path):
    leader = get_leader(db_path, leader_where(db_path, "unit_type = 'company'"))
    db = ScopedDB.for_leader(db_path, leader)
    try:
        text = db.schema_text()
        for col in ["attrition", "monthly_income", "team", "is_manager"]:
            assert col in text
        assert "org_unit_id" not in text
    finally:
        db.close()
