import duckdb
from conftest import leader_where

from workforce.agent import WorkforceAgent, parse_sql_reply
from workforce.llm import FakeLLM

HEADCOUNT_SQL = '{"sql": "SELECT department, COUNT(*) AS headcount FROM employees GROUP BY department"}'


def dept_headcounts(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    rows = dict(con.execute("SELECT department, COUNT(*) FROM employees GROUP BY 1").fetchall())
    con.close()
    return rows


def ceo(db_path):
    return leader_where(db_path, "unit_type = 'company'")


def test_happy_path(db_path):
    sales = dept_headcounts(db_path)["Sales"]
    llm = FakeLLM([HEADCOUNT_SQL, f"Sales has {sales} people."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "How many people per department?")
    assert res.status == "answered", res.steps
    assert res.answer == f"Sales has {sales} people."
    assert res.verification["passed"]
    assert res.llm_calls == 2 and res.input_tokens > 0


def test_rejected_sql_is_retried(db_path):
    llm = FakeLLM(['{"sql": "SELECT * FROM employees"}', HEADCOUNT_SQL, "Here are the headcounts."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Headcount by department?")
    assert res.status == "answered"
    assert any("attempt 1 rejected" in s for s in res.steps)
    # the retry prompt tells the model why it was rejected
    assert "SELECT * is not allowed" in llm.prompts[1][1]


def test_database_error_is_retried(db_path):
    llm = FakeLLM(['{"sql": "SELECT no_such_col, COUNT(*) FROM employees GROUP BY 1"}',
                   HEADCOUNT_SQL, "Headcounts are shown."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Headcount?")
    assert res.status == "answered"
    assert any("database:" in s for s in res.steps)


def test_refusal(db_path):
    llm = FakeLLM(['{"refuse": "I can only report on groups, not on named people."}'])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "What does employee 42 earn?")
    assert res.status == "refused" and res.sql is None and res.llm_calls == 1


def test_made_up_numbers_fall_back_to_table(db_path):
    llm = FakeLLM([HEADCOUNT_SQL, "Sales has 99999 people.", "Sales has 88888 people."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Headcount by department?")
    assert res.status == "fallback"
    assert "99999" not in res.answer and "Here is the result" in res.answer
    assert res.verification["unsupported"] == ["88888"]


def test_tiny_team_leader_is_blocked_before_answering(db_path):
    llm = FakeLLM([])
    res = WorkforceAgent(db_path, llm).ask(leader_where(db_path, "headcount < 5"), "Headcount?")
    assert res.status == "blocked" and res.llm_calls == 0


def test_all_groups_hidden(db_path):
    # monthly_income is almost unique per person, so every group has 1 or 2 people
    llm = FakeLLM(['{"sql": "SELECT monthly_income, COUNT(*) FROM employees GROUP BY monthly_income"}'])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Headcount per salary?")
    assert res.status == "no_data" and res.hidden_groups > 0 and res.rows == []


def test_unparseable_replies_give_error(db_path):
    llm = FakeLLM(["not json", "still not json"])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "?")
    assert res.status == "error"


def test_non_leader_gets_error_not_crash(db_path):
    res = WorkforceAgent(db_path, FakeLLM([])).ask(-1, "?")
    assert res.status == "error" and "AccessDenied" in res.steps[-1]


def test_parse_reply_with_code_fence():
    assert parse_sql_reply('```json\n{"sql": "SELECT 1"}\n```') == {"sql": "SELECT 1"}


def test_reasoning_tags_are_stripped():
    from workforce.llm import strip_reasoning

    raw = '<think>\nlet me count {x}\n</think>\n{"sql": "SELECT 1"}'
    assert strip_reasoning(raw) == '{"sql": "SELECT 1"}'
