import pandas as pd

from workforce.verify import extract_numbers, verify_answer

ROWS = pd.DataFrame({
    "team": ["Sales / Sales Executive / Team 3", "Sales / Sales Executive / Team 4"],
    "attrition_rate": [0.1384, 0.2],
    "avg_monthly_income": [6503.12, 7000.0],
    "group_size": [12, 40],
})


def test_extract_numbers():
    assert extract_numbers("13.8% of 1,470 people, team 3, -2") == ["13.8", "1,470", "3", "-2"]


def test_numbers_from_result_pass():
    v = verify_answer("Team 3 has 13.8% attrition and an average income of 6,503 across 12 people.",
                      ROWS)
    assert v.passed, v.unsupported


def test_rounding_and_percent_scaling():
    assert verify_answer("Attrition is 14% in Team 3.", ROWS).passed
    assert verify_answer("Attrition is 20.0% in Team 4.", ROWS).passed


def test_invented_number_fails():
    v = verify_answer("Team 3 has 15.2% attrition.", ROWS)
    assert not v.passed and v.unsupported == ["15.2"]


def test_row_count_and_question_numbers_pass():
    assert verify_answer("There are 2 teams above level 4.", ROWS, question="teams above level 4").passed


def test_summed_numbers_fail():
    # 12 + 40 = 52 is not in the result; the answer must not do arithmetic
    assert not verify_answer("In total 52 people are covered.", ROWS).passed
