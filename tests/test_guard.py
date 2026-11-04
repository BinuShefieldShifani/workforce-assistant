import pytest

from workforce.guard import SQLRejected, guard

ALLOWED = [
    "SELECT department, ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS p "
    "FROM employees GROUP BY department",
    "SELECT COUNT(*) AS headcount FROM employees",
    "SELECT COUNT(DISTINCT employee_id) FROM employees",
    "SELECT team, AVG(monthly_income) FROM employees GROUP BY team",
    "SELECT AVG(CAST(attrition AS INT)) FROM employees",
    "SELECT manager_id, COUNT(*) FROM employees GROUP BY manager_id",
    "SELECT AVG(IF(over_time, 1, 0)) FROM employees WHERE job_level >= 2",
    "SELECT job_role, COUNT(*) FROM employees GROUP BY job_role HAVING COUNT(*) > 10 ORDER BY 2 DESC",
]

REJECTED = {
    "SELECT * FROM employees": "SELECT *",
    "SELECT age FROM employees": "must aggregate",
    "SELECT MAX(monthly_income) FROM employees": "MAX",
    "SELECT MEDIAN(monthly_income) FROM employees": "MEDIAN",
    "SELECT SUM(CASE WHEN employee_id = 42 THEN monthly_income END) FROM employees": "indicator",
    "SELECT SUM(monthly_income * CAST(age = 59 AS INT)) FROM employees": "column or a CASE",
    "SELECT AVG(CASE WHEN manager_id = 3 THEN 1 ELSE 0 END) FROM employees": "manager_id",
    "SELECT COUNT(*) FROM employees WHERE employee_id = 1": "employee_id",
    "SELECT COUNT(*) FROM employees e JOIN org_units u ON TRUE": "joins",
    "SELECT COUNT(*) FROM org_units": "employees table only",
    "SELECT COUNT(*) FROM read_csv('x.csv')": "employees table only",
    "SELECT COUNT(*) FROM (SELECT * FROM employees)": "subqueries",
    "WITH a AS (SELECT 1) SELECT COUNT(*) FROM employees": "CTE",
    "SELECT COUNT(*) FROM employees UNION SELECT COUNT(*) FROM employees": "SELECT",
    "SELECT COUNT(*) FILTER (WHERE attrition) FROM employees": "FILTER",
    "SELECT department, COUNT(*) OVER () FROM employees": "window",
    "DROP TABLE employees": "only SELECT",
    "SELECT COUNT(*) FROM employees; SELECT 1": "one SQL statement",
    "SELEC COUNT(*) FRM employees": "parse|only SELECT|one SQL",
}


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    g = guard(sql)
    assert "group_size" in g.sql


@pytest.mark.parametrize("sql,reason", REJECTED.items())
def test_rejected(sql, reason):
    with pytest.raises(SQLRejected, match=reason):
        guard(sql)


def test_group_size_and_limit_added():
    g = guard("SELECT team, COUNT(*) AS n FROM employees GROUP BY team")
    assert "COUNT(*) AS group_size" in g.sql
    assert g.sql.endswith("LIMIT 200")
    assert g.metric_columns == ["n"]
    assert g.where_sql is None


def test_large_limit_is_capped_small_limit_kept():
    assert guard("SELECT team, COUNT(*) FROM employees GROUP BY team LIMIT 9999").sql.endswith("LIMIT 200")
    assert guard("SELECT team, COUNT(*) FROM employees GROUP BY team LIMIT 5").sql.endswith("LIMIT 5")


def test_where_is_extracted():
    g = guard("SELECT COUNT(*) FROM employees WHERE department = 'Sales' AND over_time")
    assert g.where_sql == "department = 'Sales' AND over_time"


def test_reserved_name():
    with pytest.raises(SQLRejected, match="reserved"):
        guard("SELECT COUNT(*) AS group_size FROM employees")
