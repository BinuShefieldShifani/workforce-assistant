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
