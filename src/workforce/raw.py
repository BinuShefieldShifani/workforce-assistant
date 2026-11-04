"""Load and clean the IBM HR Attrition CSV from Kaggle."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = [
    "Age", "Attrition", "BusinessTravel", "DailyRate", "Department", "DistanceFromHome",
    "Education", "EducationField", "EmployeeCount", "EmployeeNumber",
    "EnvironmentSatisfaction", "Gender", "HourlyRate", "JobInvolvement", "JobLevel",
    "JobRole", "JobSatisfaction", "MaritalStatus", "MonthlyIncome", "MonthlyRate",
    "NumCompaniesWorked", "Over18", "OverTime", "PercentSalaryHike", "PerformanceRating",
    "RelationshipSatisfaction", "StandardHours", "StockOptionLevel", "TotalWorkingYears",
    "TrainingTimesLastYear", "WorkLifeBalance", "YearsAtCompany", "YearsInCurrentRole",
    "YearsSinceLastPromotion", "YearsWithCurrManager",
]

# Same value on every row, so they add nothing
CONSTANT_COLUMNS = ["EmployeeCount", "Over18", "StandardHours"]
# Unexplained synthetic rates that would only confuse text-to-SQL. MonthlyIncome stays.
NOISE_COLUMNS = ["DailyRate", "HourlyRate", "MonthlyRate"]
# This now comes from the generated org tree, so the original would contradict it
CONFLICTING_COLUMNS = ["YearsWithCurrManager"]

YES_NO_COLUMNS = ["attrition", "over_time"]

EXPECTED_ROWS = 1470


def to_snake(name: str) -> str:
    """'TotalWorkingYears' -> 'total_working_years'."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def load_raw(path: Path) -> pd.DataFrame:
    """Read the Kaggle CSV, check its shape and return a cleaned DataFrame.

    The result has snake_case columns, an `employee_id` key and real booleans
    for `attrition` and `over_time`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Download the IBM HR Attrition dataset from Kaggle "
            "and put WA_Fn-UseC_-HR-Employee-Attrition.csv in data/raw/ (see README)."
        )

    # utf-8-sig strips the byte-order mark some copies of this file have
    df = pd.read_csv(path, encoding="utf-8-sig")

    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing expected columns: {missing}")

    if df["EmployeeNumber"].duplicated().any():
        raise ValueError("EmployeeNumber is not unique")

    df = df.drop(columns=CONSTANT_COLUMNS + NOISE_COLUMNS + CONFLICTING_COLUMNS)
    df.columns = [to_snake(c) for c in df.columns]
    df = df.rename(columns={"employee_number": "employee_id"})

    for col in YES_NO_COLUMNS:
        values = set(df[col].unique())
        if not values <= {"Yes", "No"}:
            raise ValueError(f"Unexpected values in {col}: {values}")
        df[col] = df[col].eq("Yes")

    df["employee_id"] = df["employee_id"].astype("int64")
    return df.sort_values("employee_id").reset_index(drop=True)
