import json

import pytest
from fastapi.testclient import TestClient

from workforce.api import create_app
from workforce.llm import FakeLLM

SQL = '{"sql": "SELECT department, COUNT(*) AS headcount FROM employees GROUP BY department"}'


def client(db_path, replies, **kw):
    return TestClient(create_app(db_path=db_path, llm=FakeLLM(list(replies)), **kw))


def test_health_and_personas(db_path):
    c = client(db_path, [])
    assert c.get("/api/health").json()["ok"] is True
    personas = c.get("/api/personas").json()
    keys = [p["key"] for p in personas]
    assert keys[0] == "ceo" and "tiny" in keys
    assert all(p["headcount"] > 0 and p["unit"] for p in personas)


def test_ask_returns_answer_table_and_trust_trail(db_path):
    c = client(db_path, [SQL, "Here are the headcounts."])
    r = c.post("/api/ask", json={"persona": "ceo", "question": "Headcount by department?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "answered" and body["rows"] and body["sql"].startswith("SELECT")
    labels = [s["label"] for s in body["trust_trail"]]
    assert labels[0].startswith("Limited to your")
    # this scripted answer quotes no numbers, so the trail says so instead of "0 numbers checked"
    assert any("quotes no numbers" in x for x in labels)
    assert not any(x.startswith("0 number") for x in labels)


def test_tiny_team_is_blocked(db_path):
    body = client(db_path, []).post("/api/ask", json={"persona": "tiny", "question": "Headcount?"}).json()
    assert body["status"] == "blocked"


def test_unknown_persona_and_bad_input(db_path):
    c = client(db_path, [])
    assert c.post("/api/ask", json={"persona": "nobody", "question": "hi there"}).status_code == 404
    assert c.post("/api/ask", json={"persona": "ceo", "question": "x" * 501}).status_code == 422


def test_rate_limit(db_path):
    c = client(db_path, [], rate_per_minute=2)
    codes = [c.post("/api/ask", json={"persona": "tiny", "question": "Headcount?"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_daily_cap(db_path):
    c = client(db_path, [], rate_per_minute=100, daily_cap=1)
    c.post("/api/ask", json={"persona": "tiny", "question": "Headcount?"})
    r = c.post("/api/ask", json={"persona": "tiny", "question": "Headcount?"})
    assert r.status_code == 429 and "daily" in r.json()["detail"]


def test_feedback_is_logged(db_path, tmp_path, monkeypatch):
    log = tmp_path / "fb.jsonl"
    monkeypatch.setenv("FEEDBACK_LOG", str(log))
    c = client(db_path, [])
    r = c.post("/api/feedback", json={"persona": "ceo", "question": "q", "status": "answered", "rating": 1})
    assert r.json() == {"ok": True}
    assert json.loads(log.read_text())["rating"] == 1


@pytest.mark.parametrize("rating", [2, -2])
def test_feedback_rating_range(db_path, rating):
    r = client(db_path, []).post("/api/feedback", json={"persona": "ceo", "question": "q",
                                                          "status": "answered", "rating": rating})
    assert r.status_code == 422


def test_null_group_values_serialise(db_path):
    # GROUP BY team has a NULL team (unit heads); it must come back as JSON null, not NaN
    sql = '{"sql": "SELECT team, COUNT(*) AS n FROM employees GROUP BY team"}'
    r = client(db_path, [sql, "Shown in the table."]).post(
        "/api/ask", json={"persona": "dept_rd", "question": "Headcount per team?"})
    assert r.status_code == 200
