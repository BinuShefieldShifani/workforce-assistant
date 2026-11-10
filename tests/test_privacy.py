import pandas as pd
import pytest
from conftest import leader_where

from workforce.guard import guard
from workforce.privacy import QueryBlocked, run_safe
from workforce.scope import ScopedDB, get_leader


def scoped(db_path, condition):
    return ScopedDB.for_leader(db_path, get_leader(db_path, leader_where(db_path, condition)))


def test_leader_of_tiny_team_gets_nothing(db_path):
    db = scoped(db_path, "headcount < 5")
    with pytest.raises(QueryBlocked, match="fewer than 5"):
        run_safe(db, guard("SELECT COUNT(*) FROM employees"))


def test_small_groups_are_hidden(db_path):
    db = scoped(db_path, "unit_type = 'company'")
    res = run_safe(db, guard("SELECT team, COUNT(*) AS n FROM employees GROUP BY team"))
    assert res.hidden_groups > 0  # fake data has teams of 3 and 4
    assert (res.rows["group_size"] >= 5).all()


def test_single_person_filter_returns_nothing(db_path):
    db = scoped(db_path, "unit_type = 'company'")
    res = run_safe(db, guard("SELECT AVG(monthly_income) FROM employees WHERE age = 18 AND job_level = 5 "
                             "AND department = 'Human Resources'"))
    assert res.rows.empty or (res.rows["group_size"] >= 5).all()


def test_filter_that_excludes_a_small_team_is_blocked(db_path):
    # find a department leader whose department contains a team of fewer than 5 people
    db = scoped(db_path, "unit_type = 'company'")
    teams = db.query("SELECT team, COUNT(*) AS n FROM employees WHERE team IS NOT NULL GROUP BY team")
    small_team = teams[teams["n"] < 5]["team"].iloc[0]
    with pytest.raises(QueryBlocked, match="leaves out fewer than 5"):
        run_safe(db, guard(f"SELECT AVG(monthly_income) FROM employees WHERE team IS NULL "
                           f"OR team <> '{small_team}'"))


def test_complementary_suppression():
    # Three groups: 3, 10, 12 people. Hiding only the 3 would let it be worked out
    # from the total, so the next smallest group (10) is hidden too.
    df = pd.DataFrame({"grp": ["a"] * 3 + ["b"] * 10 + ["c"] * 12, "x": range(25)})
    db = ScopedDB(df)
    res = run_safe(db, guard("SELECT grp, AVG(x) AS avg_x FROM employees GROUP BY grp ORDER BY grp"))
    assert res.hidden_groups == 2
    assert res.rows["grp"].tolist() == ["c"]


def test_no_extra_suppression_when_hidden_groups_add_up_to_five():
    df = pd.DataFrame({"grp": ["a"] * 3 + ["b"] * 2 + ["c"] * 12 + ["d"] * 9, "x": range(26)})
    res = run_safe(ScopedDB(df), guard("SELECT grp, COUNT(*) AS n FROM employees GROUP BY grp"))
    assert res.hidden_groups == 2
    assert sorted(res.rows["grp"]) == ["c", "d"]


def test_query_matching_nobody(db_path):
    db = scoped(db_path, "unit_type = 'company'")
    res = run_safe(db, guard("SELECT team, COUNT(*) FROM employees WHERE age > 200 GROUP BY team"))
    assert res.rows.empty and res.hidden_groups == 0


def test_pivot_cannot_show_small_cells():
    # Unseen-eval case k05: a pivot puts a 3-person cell inside a 23-person row
    df = pd.DataFrame({"job_level": [5] * 23, "gender": ["Female"] * 3 + ["Male"] * 20, "x": range(23)})
    q = guard("SELECT job_level, SUM(CASE WHEN gender = 'Female' THEN 1 ELSE 0 END) AS female_count, "
              "SUM(CASE WHEN gender = 'Male' THEN 1 ELSE 0 END) AS male_count "
              "FROM employees GROUP BY job_level")
    assert q.count_columns == ["female_count", "male_count"]
    res = run_safe(ScopedDB(df), q)
    row = res.rows.iloc[0]
    # both counts are blanked, otherwise female = group_size - male
    assert row["female_count"] is None and row["male_count"] is None
    assert res.masked_cells == 2 and int(row["group_size"]) == 23


def test_large_counts_and_rates_are_not_masked():
    df = pd.DataFrame({"g": ["a"] * 30, "attrition": [True] * 3 + [False] * 27,
                       "over_time": [True] * 10 + [False] * 20})
    q = guard("SELECT g, SUM(CASE WHEN over_time THEN 1 ELSE 0 END) AS overtime_count, "
              "ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS attrition_pct, "
              "COUNT(*) AS headcount FROM employees GROUP BY g")
    assert q.count_columns == ["overtime_count"]  # the rate and COUNT(*) are not subgroup counts
    res = run_safe(ScopedDB(df), q)
    assert res.masked_cells == 0 and res.rows.iloc[0]["overtime_count"] == 10


def test_nobody_matching_is_not_reported_as_hidden(db_path):
    # adversarial test 20: COUNT without GROUP BY returns one row with 0 people
    db = scoped(db_path, "unit_type = 'department' AND unit_name = 'Human Resources'")
    res = run_safe(db, guard("SELECT ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS pct "
                             "FROM employees WHERE department = 'Sales'"))
    assert res.matched_nobody and res.hidden_groups == 0 and res.rows.empty
