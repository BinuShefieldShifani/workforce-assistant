import duckdb

from workforce.db import load_duckdb
from workforce.org import build_org, write_processed
from workforce.raw import load_raw


def test_duckdb_load(tmp_path, fake_csv):
    org = build_org(load_raw(fake_csv), seed=42)
    write_processed(org, tmp_path / "processed")
    db = tmp_path / "test.duckdb"

    counts = load_duckdb(tmp_path / "processed", db)
    assert counts == {
        "employees": len(org.employees),
        "org_units": len(org.org_units),
        "org_closure": len(org.org_closure),
    }

    con = duckdb.connect(str(db), read_only=True)
    try:
        types = dict(con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'employees'"
        ).fetchall())
        assert types["attrition"] == "BOOLEAN"
        assert types["manager_id"] == "BIGINT"

        root_hc = con.execute(
            "SELECT h.headcount FROM unit_headcount h JOIN org_units u USING (org_unit_id) "
            "WHERE u.parent_unit_id IS NULL"
        ).fetchone()[0]
        assert root_hc == len(org.employees)

        n_leaders = con.execute("SELECT COUNT(*) FROM leaders").fetchone()[0]
        assert n_leaders == len(org.org_units)
    finally:
        con.close()


def test_reload_replaces_tables(tmp_path, fake_csv):
    org = build_org(load_raw(fake_csv), seed=42)
    write_processed(org, tmp_path / "p")
    db = tmp_path / "t.duckdb"
    load_duckdb(tmp_path / "p", db)
    counts = load_duckdb(tmp_path / "p", db)  # second run must not duplicate rows
    assert counts["employees"] == len(org.employees)
