"""Test fixtures. CI has no Kaggle access, so tests use a fake CSV with the
same columns and value ranges as the real file."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from workforce.raw import EXPECTED_COLUMNS

ROLES = {
    "Research & Development": [
        "Research Scientist", "Laboratory Technician", "Manufacturing Director",
        "Healthcare Representative", "Research Director", "Manager",
    ],
    "Sales": ["Sales Executive", "Sales Representative", "Manager"],
    "Human Resources": ["Human Resources", "Manager"],
}


def make_fake_raw(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    depts = rng.choice(list(ROLES), size=n, p=[0.65, 0.30, 0.05])
    roles = [rng.choice(ROLES[d]) for d in depts]
    yes_no = lambda p: rng.choice(["Yes", "No"], size=n, p=[p, 1 - p])  # noqa: E731
    data = {
        "Age": rng.integers(18, 61, n),
        "Attrition": yes_no(0.16),
        "BusinessTravel": rng.choice(["Travel_Rarely", "Travel_Frequently", "Non-Travel"], n),
        "DailyRate": rng.integers(100, 1500, n),
        "Department": depts,
        "DistanceFromHome": rng.integers(1, 30, n),
        "Education": rng.integers(1, 6, n),
        "EducationField": rng.choice(["Life Sciences", "Medical", "Marketing", "Other"], n),
        "EmployeeCount": 1,
        "EmployeeNumber": rng.permutation(np.arange(1, 3 * n))[:n],
        "EnvironmentSatisfaction": rng.integers(1, 5, n),
        "Gender": rng.choice(["Male", "Female"], n),
        "HourlyRate": rng.integers(30, 100, n),
        "JobInvolvement": rng.integers(1, 5, n),
        "JobLevel": rng.integers(1, 6, n),
        "JobRole": roles,
        "JobSatisfaction": rng.integers(1, 5, n),
        "MaritalStatus": rng.choice(["Single", "Married", "Divorced"], n),
        "MonthlyIncome": rng.integers(1000, 20000, n),
        "MonthlyRate": rng.integers(2000, 27000, n),
        "NumCompaniesWorked": rng.integers(0, 10, n),
        "Over18": "Y",
        "OverTime": yes_no(0.28),
        "PercentSalaryHike": rng.integers(11, 26, n),
        "PerformanceRating": rng.integers(3, 5, n),
        "RelationshipSatisfaction": rng.integers(1, 5, n),
        "StandardHours": 80,
        "StockOptionLevel": rng.integers(0, 4, n),
        "TotalWorkingYears": rng.integers(0, 41, n),
        "TrainingTimesLastYear": rng.integers(0, 7, n),
        "WorkLifeBalance": rng.integers(1, 5, n),
        "YearsAtCompany": rng.integers(0, 41, n),
        "YearsInCurrentRole": rng.integers(0, 19, n),
        "YearsSinceLastPromotion": rng.integers(0, 16, n),
        "YearsWithCurrManager": rng.integers(0, 18, n),
    }
    return pd.DataFrame(data)[EXPECTED_COLUMNS]


@pytest.fixture
def fake_csv(tmp_path):
    path = tmp_path / "hr.csv"
    make_fake_raw().to_csv(path, index=False)
    return path


@pytest.fixture
def db_path(tmp_path):
    """A DuckDB built from fake data, the same way the real one is built."""
    from workforce.db import load_duckdb
    from workforce.org import build_org, write_processed
    from workforce.raw import load_raw

    csv = tmp_path / "hr.csv"
    make_fake_raw(n=600, seed=1).to_csv(csv, index=False)
    org = build_org(load_raw(csv), seed=42)
    write_processed(org, tmp_path / "processed")
    path = tmp_path / "test.duckdb"
    load_duckdb(tmp_path / "processed", path)
    return path


def leader_where(db_path, sql: str) -> int:
    """Return the first leader_id matching a condition on the leaders view."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(f"SELECT leader_id FROM leaders WHERE {sql} ORDER BY leader_id").fetchone()
    finally:
        con.close()
    assert row is not None, f"no leader where {sql}"
    return int(row[0])
