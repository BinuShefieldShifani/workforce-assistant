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
    res = WorkforceAgent(db_path, llm, fallback_version="none").ask(ceo(db_path), "Headcount?")
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


def test_prompt_versions_differ_and_are_selectable(db_path):
    from workforce.prompts import get_prompts

    v1, v2 = get_prompts("v1"), get_prompts("v2")
    assert v1.hash != v2.hash
    assert "team IS NULL OR team" in v2.sql_system and "team IS NULL" not in v1.sql_system
    assert WorkforceAgent(db_path, FakeLLM([]), prompt_version="v1").prompts.version == "v1"


def test_v3_adds_group_by_and_null_guidance():
    from workforce.prompts import get_prompts

    v2, v3 = get_prompts("v2"), get_prompts("v3")
    assert v3.hash != v2.hash
    assert "GROUP BY that column" in v3.sql_system and "GROUP BY that column" not in v2.sql_system
    assert "IS NOT NULL" in v3.sql_system
    assert v3.answer_system == v2.answer_system


# ---------- fixes from the adversarial tests ----------

def test_help_question_gets_fixed_answer_without_llm(db_path):
    res = WorkforceAgent(db_path, FakeLLM([])).ask(ceo(db_path), "What can you do?")
    assert res.status == "help" and res.llm_calls == 0 and "attrition" in res.answer


def test_people_decision_refused_in_code(db_path):
    res = WorkforceAgent(db_path, FakeLLM([])).ask(ceo(db_path), "Which team should I cut to save the most money?")
    assert res.status == "refused" and res.llm_calls == 0


def test_recommendation_in_answer_is_rejected(db_path):
    sql = '{"sql": "SELECT team, SUM(monthly_income) AS total_monthly_income FROM employees GROUP BY team"}'
    llm = FakeLLM([sql, "Cut Team 4 to save money.", "Cut Team 4, it costs the most."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "What is the total monthly income per team?")
    assert res.status == "fallback" and "Cut" not in res.answer
    assert "recommends an action" in " ".join(res.steps)


def test_unrequested_pay_column_is_sent_back(db_path):
    with_pay = ('{"sql": "SELECT gender, ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS pct, '
                'AVG(monthly_income) AS pay FROM employees GROUP BY gender"}')
    without = ('{"sql": "SELECT gender, ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS pct '
               'FROM employees GROUP BY gender"}')
    llm = FakeLLM([with_pay, without, "Attrition by gender is shown in the table."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Is attrition different for men and women?")
    assert res.status == "answered" and "monthly_income" not in res.sql
    assert "does not ask about monthly_income" in llm.prompts[1][1]


def test_refused_question_shows_no_sql(db_path):
    llm = FakeLLM(['{"sql": "SELECT COUNT(*) OVER () FROM employees"}',
                   '{"sql": "SELECT COUNT(*) OVER () FROM employees"}'])
    res = WorkforceAgent(db_path, llm, fallback_version="none").ask(ceo(db_path), "Headcount and more?")
    assert res.status == "error" and res.sql is None
    assert "SQL" not in res.answer and "query" not in res.answer.lower()


def test_multi_part_question_is_split(db_path):
    parts = '{"parts": ["What is our total headcount?", "What is our overall attrition rate?"]}'
    llm = FakeLLM([
        parts,
        '{"sql": "SELECT COUNT(*) AS headcount FROM employees"}', "Shown in the table.",
        '{"sql": "SELECT ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS pct FROM employees"}',
        "Shown in the table.",
    ])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Total headcount and overall attrition?")
    assert res.status == "answered" and len(res.parts) == 2
    assert [p.status for p in res.parts] == ["answered", "answered"]
    assert res.parts[0].rows and res.parts[1].sql


def test_vague_question_states_its_assumption(db_path):
    llm = FakeLLM(['{"sql": "SELECT COUNT(*) AS headcount FROM employees WHERE department = \'Sales\'", '
                   '"assumption": "a general overview of the Sales department"}', "Sales is in the table."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "And what about Sales?")
    assert res.answer.startswith("I took this to mean: a general overview of the Sales department.")


def test_nobody_matching_gets_scope_message(db_path):
    hr = leader_where(db_path, "unit_type = 'department' AND unit_name = 'Human Resources'")
    llm = FakeLLM(['{"sql": "SELECT COUNT(*) AS n FROM employees WHERE department = \'Sales\'"}'])
    res = WorkforceAgent(db_path, llm).ask(hr, "Wie hoch ist die Fluktuation in der Vertriebsabteilung?")
    assert res.status == "no_data" and "Nobody in your organisation" in res.answer
    assert "fewer than 5" not in res.answer


def test_falls_back_to_v3_when_v4_cannot_write_allowed_sql(db_path):
    bad = '{"sql": "SELECT * FROM employees"}'
    llm = FakeLLM([bad, bad, HEADCOUNT_SQL, "Headcounts are in the table."])
    res = WorkforceAgent(db_path, llm, prompt_version="v4", fallback_version="v3").ask(
        ceo(db_path), "Headcount by department?")
    assert res.status == "answered" and res.prompt_version == "v3"
    assert any("retried with fallback prompts v3" in s for s in res.steps)


def test_no_fallback_after_an_exception(db_path):
    res = WorkforceAgent(db_path, FakeLLM([]), fallback_version="v3").ask(-1, "Headcount?")
    assert res.status == "error" and not any("fallback" in s for s in res.steps)


def test_old_prompts_are_unchanged():
    from workforce.prompts import get_prompts

    # v3's hash is recorded in the committed eval results; it must never change
    assert get_prompts("v3").hash == "938ffb6674"
    assert get_prompts().version == "v4"


def test_unit_request_is_answered_in_plain_numbers(db_path):
    # a03 on v4: the model refused "in euros" outright; now the unit is removed in code
    sql = '{"sql": "SELECT department, ROUND(AVG(monthly_income), 1) AS avg_monthly_income FROM employees GROUP BY department"}'
    llm = FakeLLM([sql, "Averages by department are in the table."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "What is the average monthly income by department in euros?")
    assert res.status == "answered" and res.answer.startswith("The data doesn't say which currency")
    assert "euros" not in llm.prompts[0][1]  # the model never saw the unit


def test_follow_up_without_assumption_says_it_was_answered_alone(db_path):
    sql = '{"sql": "SELECT COUNT(*) AS headcount FROM employees WHERE department = \'Sales\'"}'
    llm = FakeLLM([sql, "Sales is in the table. The data shows what happened, not why."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "And what about Sales?")
    assert res.answer == "I can't see earlier questions, so I answered this on its own. Sales is in the table."


def test_why_question_gets_the_line(db_path):
    llm = FakeLLM([HEADCOUNT_SQL, "Headcounts are in the table."])
    res = WorkforceAgent(db_path, llm).ask(ceo(db_path), "Why is headcount so different between departments?")
    assert res.answer.endswith("This data shows the figures, not the reasons behind them.")
