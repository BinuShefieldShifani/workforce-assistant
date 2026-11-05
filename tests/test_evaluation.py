import json
import re
import sys

from workforce.agent import WorkforceAgent
from workforce.config import ROOT
from workforce.evaluation import (
    compare_rows,
    fill,
    load_golden,
    resolve_placeholders,
    run_eval,
    run_reference,
    summarize,
)
from workforce.guard import guard
from workforce.llm import FakeLLM

GOLDEN = ROOT / "eval" / "golden.yaml"


# ---------- compare_rows ----------

def test_rows_match_despite_names_order_and_extra_columns():
    agent = [{"dept": "Sales", "rate": 20.63, "headcount": 446, "group_size": 446},
             {"dept": "HR", "rate": 19.0, "headcount": 63, "group_size": 63}]
    ref = [{"department": "HR", "attrition_rate_pct": 19.0},
           {"department": "Sales", "attrition_rate_pct": 20.6}]
    assert compare_rows(agent, ref) == (True, "match")


def test_fraction_and_percent_count_as_equal():
    assert compare_rows([{"r": 0.206}], [{"r": 20.6}])[0]


def test_wrong_value_or_row_count_fails():
    assert not compare_rows([{"r": 21.9}], [{"r": 20.6}])[0]
    ok, note = compare_rows([{"r": 1}, {"r": 2}], [{"r": 1}])
    assert not ok and "row count" in note


def test_renamed_group_labels_match_when_consistent():
    # b15 in the first v3 run: the model labelled the groups 'Leavers'/'Stayers'
    agent = [{"status": "Leavers", "avg": 10.8, "headcount": 92, "group_size": 92},
             {"status": "Stayers", "avg": 9.0, "headcount": 354, "group_size": 354}]
    ref = [{"attrition": False, "avg_distance": 9.0, "group_size": 354},
           {"attrition": True, "avg_distance": 10.8, "group_size": 92}]
    ok, note = compare_rows(agent, ref)
    assert ok and "false->stayers" in note and "true->leavers" in note


def test_renamed_labels_must_match_the_numbers():
    # same labels, but the numbers belong to the other group: must fail
    agent = [{"status": "Leavers", "avg": 9.0, "group_size": 354},
             {"status": "Stayers", "avg": 10.8, "group_size": 92}]
    ref = [{"attrition": True, "avg_distance": 9.0, "group_size": 92},
           {"attrition": False, "avg_distance": 10.8, "group_size": 354}]
    assert not compare_rows(agent, ref)[0]


def test_booleans_and_nulls():
    assert compare_rows([{"over_time": True, "n": 5}], [{"over_time": True, "n": 5}])[0]
    assert compare_rows([{"team": None, "n": 7}], [{"team": None, "n": 7}])[0]
    assert not compare_rows([{"team": None, "n": 7}], [{"team": "x", "n": 7}])[0]


# ---------- golden file ----------

def test_golden_file_is_valid(db_path):
    cases = load_golden(GOLDEN)
    assert len(cases) == 40
    values = resolve_placeholders(db_path)
    assert all(values[c.leader] is not None for c in cases)
    for c in cases:
        if c.reference_sql:
            sql = fill(c.reference_sql, values)
            guard(sql)  # every reference query follows the same rules as the agent
            status, _ = run_reference(db_path, int(values[c.leader]), sql)
            assert status in {"answered", "no_data", "blocked"}


# ---------- full run with scripted models ----------

def scripted_llm(db_path, override=None):
    """Plays a model that answers each golden question with the reference SQL, or refuses."""
    values = resolve_placeholders(db_path)
    by_question = {fill(c.question, values): c for c in load_golden(GOLDEN)}
    override = override or {}

    def reply(system, user):
        if "answer a leader's question" in system:
            return "Here is the result."
        q = re.search(r"Question: (.*)", user).group(1).strip()
        case = by_question[q]
        if case.id in override:
            return override[case.id]
        if case.reference_sql:
            return json.dumps({"sql": " ".join(fill(case.reference_sql, values).split())})
        if "answered" in case.expect:
            sql = ("SELECT team, ROUND(100 * AVG(CASE WHEN attrition THEN 1 ELSE 0 END), 1) AS pct "
                   "FROM employees GROUP BY team HAVING COUNT(*) >= 5 ORDER BY pct DESC LIMIT 1")
            return json.dumps({"sql": sql})
        return json.dumps({"refuse": "I can't answer that."})

    class Scripted(FakeLLM):
        def complete(self, system, user):
            self.replies = [reply]
            return super().complete(system, user)

    return Scripted()


def test_perfect_model_passes_everything(db_path):
    agent = WorkforceAgent(db_path, scripted_llm(db_path))
    results = run_eval(agent, load_golden(GOLDEN), log=lambda *_: None)
    failed = [(r.id, r.status, r.result_note, r.style_issues) for r in results if not r.passed]
    assert failed == []
    s = summarize(results)
    assert s["pass_rate"] == 100.0 and s["result_accuracy"] == 100.0


def test_null_bug_is_caught(db_path):
    values = resolve_placeholders(db_path)
    bad = json.dumps({"sql": "SELECT ROUND(AVG(monthly_income), 1) AS avg_monthly_income "
                             f"FROM employees WHERE team <> '{values['small_team_rd']}'"})
    agent = WorkforceAgent(db_path, scripted_llm(db_path, {"p06": bad}))
    cases = [c for c in load_golden(GOLDEN) if c.id == "p06"]
    [r] = run_eval(agent, cases, log=lambda *_: None)
    assert r.status == "answered" and not r.passed
    assert "reference is blocked" in r.result_note


def test_currency_symbol_is_flagged(db_path):
    llm = scripted_llm(db_path)
    agent = WorkforceAgent(db_path, llm)
    cases = [c for c in load_golden(GOLDEN) if c.id == "b01"]
    original = llm.complete

    def with_dollar(system, user):
        out = original(system, user)
        return "It is $ shown in the table." if "answer a leader's question" in system else out

    llm.complete = with_dollar
    [r] = run_eval(agent, cases, log=lambda *_: None)
    assert not r.passed and r.style_issues == ["currency symbol in answer"]


def test_report_renders(db_path, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_report

    from workforce.evaluation import results_to_dict

    agent = WorkforceAgent(db_path, scripted_llm(db_path))
    results = run_eval(agent, load_golden(GOLDEN)[:5], log=lambda *_: None)
    run = {"meta": {"model": "fake", "prompt_version": "v2", "prompt_hash": "x", "git_commit": "x",
                    "timestamp": "t", "golden_hash": "g"},
           "summary": summarize(results), "results": results_to_dict(results)}
    text = make_report.render(run, baseline=run)
    assert "# Evaluation report" in text and "| Metric | Baseline | This run | Change |" in text
