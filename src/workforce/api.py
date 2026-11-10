"""HTTP API for the web app.

    GET  /api/health      liveness, and whether an LLM key is configured
    GET  /api/personas    the demo leaders you can ask as
    POST /api/ask         {persona, question} -> answer, status, table, SQL, trust trail
    POST /api/feedback    {question, status, rating, comment} -> logged

Demo rules: you pick one of a few personas instead of logging in (a real version
would map the signed-in user to their unit via SSO), questions are rate-limited
per visitor and per day to protect the free LLM tier, and every question is logged
as one JSON line so we can see what people actually ask.

Configuration (environment variables):
    WORKFORCE_DB      path to the DuckDB file (default data/workforce.duckdb)
    ALLOWED_ORIGINS   comma-separated origins for CORS, e.g. https://your-app.vercel.app
    RATE_PER_MINUTE   questions per visitor per minute (default 6)
    DAILY_CAP         questions per day for the whole demo (default 400)
    FEEDBACK_LOG      where feedback lines go (default /tmp/workforce_feedback.jsonl)
plus the LLM variables from .env.example.

Run locally:  uvicorn workforce.api:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from workforce.agent import WorkforceAgent
from workforce.config import DUCKDB_PATH
from workforce.evaluation import PLACEHOLDER_SQL
from workforce.llm import LLM

log = logging.getLogger("workforce.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

PERSONAS = [
    ("ceo", "CEO", "the whole company"),
    ("dept_rd", "Head of Research & Development", "a large department"),
    ("dept_sales", "Head of Sales", "a mid-size department"),
    ("dept_hr", "Head of Human Resources", "a small department"),
    ("func_sales_exec", "Sales Executive function lead", "one function with several teams"),
    ("tiny", "Lead of a 3-person team", "too small to report on"),
]
MAX_ROWS_RETURNED = 50


class AskRequest(BaseModel):
    persona: str = Field(..., max_length=40)
    question: str = Field(..., min_length=2, max_length=500)


class FeedbackRequest(BaseModel):
    persona: str = Field(..., max_length=40)
    question: str = Field(..., max_length=500)
    status: str = Field(..., max_length=20)
    rating: int = Field(..., ge=-1, le=1)
    comment: str = Field("", max_length=1000)


class RateLimiter:
    def __init__(self, per_minute: int, daily_cap: int):
        self.per_minute, self.daily_cap = per_minute, daily_cap
        self.hits: dict[str, deque] = defaultdict(deque)
        self.day, self.day_count = date.today(), 0
        self.lock = threading.Lock()

    def check(self, key: str) -> str | None:
        now = time.monotonic()
        with self.lock:
            if date.today() != self.day:
                self.day, self.day_count = date.today(), 0
            if self.day_count >= self.daily_cap:
                return "The demo has reached its daily question limit. Please try again tomorrow."
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return "Too many questions in a minute. Please wait a moment and try again."
            q.append(now)
            self.day_count += 1
            return None


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def trust_trail(result, unit_size: int | None) -> list[dict]:
    """Plain-language steps the UI shows under each answer."""
    steps = []
    if unit_size is not None:
        steps.append({"label": f"Limited to your {unit_size} people", "ok": True})
    if result.status in ("answered", "fallback", "no_data"):
        steps.append({"label": "Passed the privacy and safety checks", "ok": True})
    if result.hidden_groups:
        steps.append({"label": f"{result.hidden_groups} group(s) hidden for privacy", "ok": True})
    if result.masked_cells:
        steps.append({"label": f"{result.masked_cells} small count(s) blanked out", "ok": True})
    if result.status == "answered" and result.verification:
        n = len(result.verification.get("checked", []))
        if n:
            steps.append({"label": f"{n} number(s) in the answer checked against the data", "ok": True})
        else:
            steps.append({"label": "The written answer quotes no numbers; the figures are in the data table",
                          "ok": True})
    if result.status == "fallback":
        steps.append({"label": "The written answer failed the checks, so the plain result is shown",
                      "ok": False})
    return steps


def serialise(result, unit_size: int | None) -> dict:
    return {
        "question": result.question,
        "status": result.status,
        "answer": result.answer,
        "assumption": result.assumption,
        "sql": result.sql,
        "rows": result.rows[:MAX_ROWS_RETURNED],
        "total_rows": len(result.rows),
        "hidden_groups": result.hidden_groups,
        "masked_cells": result.masked_cells,
        "trust_trail": trust_trail(result, unit_size) if result.status != "help" else [],
        "parts": [serialise(p, None) for p in result.parts],
        "latency_ms": result.latency_ms,
    }


def create_app(db_path: Path | None = None, llm: LLM | None = None,
               rate_per_minute: int | None = None, daily_cap: int | None = None) -> FastAPI:
    db_path = Path(db_path or os.getenv("WORKFORCE_DB", DUCKDB_PATH))
    state: dict = {"llm": llm, "personas": None}
    limiter = RateLimiter(rate_per_minute or int(os.getenv("RATE_PER_MINUTE", "6")),
                          daily_cap or int(os.getenv("DAILY_CAP", "400")))
    feedback_log = Path(os.getenv("FEEDBACK_LOG", "/tmp/workforce_feedback.jsonl"))

    app = FastAPI(title="Workforce Assistant API", version="1.0")
    allowed = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
    origins = [o.strip() for o in allowed.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    def get_llm() -> LLM:
        if state["llm"] is None:
            from workforce.llm import get_llm as make_llm
            state["llm"] = make_llm()
        return state["llm"]

    def personas() -> dict[str, dict]:
        if state["personas"] is None:
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                out = {}
                for key, title, blurb in PERSONAS:
                    row = con.execute(PLACEHOLDER_SQL[key]).fetchone()
                    if not row:
                        continue
                    leader = con.execute("SELECT leader_id, unit_name, headcount FROM leaders "
                                         "WHERE leader_id = ?", [row[0]]).fetchone()
                    out[key] = {"key": key, "title": title, "blurb": blurb, "leader_id": int(leader[0]),
                                "unit": leader[1], "headcount": int(leader[2])}
                state["personas"] = out
            finally:
                con.close()
        return state["personas"]

    @app.get("/api/health")
    def health():
        has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or state["llm"])
        return {"ok": db_path.exists(), "llm_configured": has_key}

    @app.get("/api/personas")
    def list_personas():
        return list(personas().values())

    @app.post("/api/ask")
    def ask(body: AskRequest, request: Request):
        persona = personas().get(body.persona)
        if persona is None:
            raise HTTPException(404, "Unknown persona")
        limited = limiter.check(client_key(request))
        if limited:
            raise HTTPException(429, limited)
        try:
            agent = WorkforceAgent(db_path, get_llm())
        except Exception as e:  # missing key or SDK
            log.error(json.dumps({"event": "llm_unavailable", "error": str(e)}))
            raise HTTPException(503, "The language model is not configured on the server.") from e

        result = agent.ask(persona["leader_id"], body.question.strip())
        log.info(json.dumps({
            "event": "ask", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "persona": body.persona, "question": body.question, "status": result.status,
            "latency_ms": result.latency_ms, "llm_calls": result.llm_calls,
            "tokens_in": result.input_tokens, "tokens_out": result.output_tokens,
            "hidden_groups": result.hidden_groups, "masked_cells": result.masked_cells,
            "parts": len(result.parts), "prompt_version": result.prompt_version,
        }))
        return serialise(result, persona["headcount"])

    @app.post("/api/feedback")
    def feedback(body: FeedbackRequest, request: Request):
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **body.model_dump()}
        try:
            with feedback_log.open("a") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            pass  # a read-only disk must not break the demo
        log.info(json.dumps({"event": "feedback", **line}))
        return {"ok": True}

    return app


app = create_app()
