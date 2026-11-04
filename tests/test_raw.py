import pandas as pd
import pytest

from workforce.raw import load_raw, to_snake


def test_to_snake():
    assert to_snake("TotalWorkingYears") == "total_working_years"
    assert to_snake("Age") == "age"


def test_load_raw_cleans_columns(fake_csv):
    df = load_raw(fake_csv)
    assert "employee_id" in df.columns
    for dropped in ["employee_count", "over18", "standard_hours", "daily_rate",
                    "years_with_curr_manager"]:
        assert dropped not in df.columns
    assert df["attrition"].dtype == bool
    assert df["over_time"].dtype == bool
    assert df["employee_id"].is_unique


def test_load_raw_handles_bom(tmp_path, fake_csv):
    bom_path = tmp_path / "bom.csv"
    bom_path.write_bytes("\ufeff".encode() + fake_csv.read_bytes())
    assert "age" in load_raw(bom_path).columns


def test_load_raw_rejects_missing_column(tmp_path, fake_csv):
    df = pd.read_csv(fake_csv).drop(columns=["JobRole"])
    path = tmp_path / "broken.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="JobRole"):
        load_raw(path)


def test_load_raw_missing_file_has_helpful_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="Kaggle"):
        load_raw(tmp_path / "nope.csv")
