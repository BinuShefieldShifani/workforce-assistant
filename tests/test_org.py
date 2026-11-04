import pandas as pd
import pytest

from workforce.org import build_org, headcount_by_unit, validate_org
from workforce.raw import load_raw


@pytest.fixture
def org(fake_csv):
    return build_org(load_raw(fake_csv), seed=42)


def test_org_is_valid(org):
    assert validate_org(org) == []


def test_everyone_is_kept(org, fake_csv):
    raw = load_raw(fake_csv)
    assert len(org.employees) == len(raw)
    assert set(org.employees["employee_id"]) == set(raw["employee_id"])


def test_four_levels(org):
    expected = {"company": 0, "department": 1, "function": 2, "team": 3}
    for unit_type, level in expected.items():
        assert (org.org_units.loc[org.org_units["unit_type"] == unit_type, "level"] == level).all()
    assert (org.org_units["unit_type"] == "company").sum() == 1


def test_company_headcount_is_everyone(org):
    hc = headcount_by_unit(org)
    root = org.org_units.loc[org.org_units["parent_unit_id"].isna(), "org_unit_id"].iloc[0]
    assert hc[root] == len(org.employees)


def test_closure_depths(org):
    c = org.org_closure
    assert c["depth"].between(0, 3).all()
    # each team has 4 ancestors including itself
    teams = org.org_units.loc[org.org_units["unit_type"] == "team", "org_unit_id"]
    per_team = c[c["descendant_unit_id"].isin(teams)].groupby("descendant_unit_id").size()
    assert (per_team == 4).all()


def test_small_groups_exist_for_suppression_tests(org):
    assert (headcount_by_unit(org) < 5).any()


def test_same_seed_same_org(fake_csv):
    raw = load_raw(fake_csv)
    a, b = build_org(raw, seed=1), build_org(raw, seed=1)
    pd.testing.assert_frame_equal(a.employees, b.employees)
    pd.testing.assert_frame_equal(a.org_units, b.org_units)


def test_validator_catches_broken_manager(org):
    emp = org.employees.copy()
    non_ceo = emp[emp["manager_id"].notna()].index[0]
    emp.loc[non_ceo, "manager_id"] = 999_999
    org.employees = emp
    assert any("unknown employees" in e for e in validate_org(org))


def test_validator_catches_cycle(org):
    emp = org.employees.copy().set_index("employee_id", drop=False)
    emp.index.name = None
    units = org.org_units
    ceo = int(units.loc[units["parent_unit_id"].isna(), "head_employee_id"].iloc[0])
    some_dept_head = int(units.loc[units["unit_type"] == "department", "head_employee_id"].iloc[0])
    emp.loc[ceo, "manager_id"] = some_dept_head
    org.employees = emp.reset_index(drop=True)
    assert validate_org(org) != []
