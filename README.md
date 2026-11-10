# Workforce Assistant

An assistant that answers leaders' questions about their own organisation in plain
language. It turns a question into SQL, runs it only on the part of the org the leader
is allowed to see, refuses to report on groups smaller than five people, and checks
every number in the answer against the query result before showing it.

Built on the IBM HR Attrition dataset from Kaggle. The data is fictional, so no real
person's data is involved. The dataset has no reporting lines, so this project
generates a synthetic org tree on top of it (company, departments, functions, teams).

## Status

- [x] Day 1: data, synthetic org hierarchy, DuckDB and Snowflake loaders, tests, CI
- [x] Day 2: access scoping and small-group suppression
- [x] Day 3: text-to-SQL agent with answer verification
- [x] Day 4: golden question set, eval runner, versioned prompts, eval report
- [x] Day 5: small-cell masking, FastAPI back end, Next.js front end, deployment (Hugging Face Spaces + Vercel)

## Data model

| Table | What it holds |
|---|---|
| `employees` | One row per person: HR attributes, `manager_id`, `org_unit_id` |
| `org_units` | Company, departments, functions and teams, with `parent_unit_id` and `head_employee_id` |
| `org_closure` | Every ancestor/descendant pair of units, so "everyone under unit X" is one join |
| `unit_headcount` (view) | Headcount per unit including everything below it |
| `leaders` (view) | Everyone who heads a unit, with their headcount |

## How a question is answered

```
question + leader id
  -> scope:   build an in-memory DuckDB with only this leader's people
              (file access and Python lookups switched off and locked)
  -> LLM:     write one SQL query, or refuse (named people, row-level lists)
  -> guard:   one SELECT on employees, aggregates only (COUNT/SUM/AVG),
              no joins/subqueries/CTEs, no MIN/MAX, no per-person tricks
              -> on rejection, the reason goes back to the LLM for one retry
  -> privacy: hide groups under 5 people, block filters that leave out
              fewer than 5, hide extra rows so hidden ones can't be recovered
  -> LLM:     write the answer using only the result
  -> verify:  every number in the answer must come from the result
              -> one retry, then a plain answer built straight from the table
```

The privacy rules live in code, not in the prompt, so they hold even when the model
writes a bad query. Every answer comes back with its status, SQL, steps, token use and
latency.

## Evaluation

`eval/golden.yaml` holds 40 questions in four groups: basic aggregates, org-structure
questions, privacy attacks and out-of-scope requests. Each says which outcomes are
correct, and 28 have a hand-written reference query. For those, the agent's result
rows must match the reference rows after both go through the same privacy rules.
Comparing rows rather than SQL text lets equivalent queries pass, and catches queries
that answer a slightly different question even when every number in the answer is
"verified".

```bash
python scripts/run_eval.py --prompt-version v1      # baseline
python scripts/run_eval.py --prompt-version v2      # with the prompt fixes
python scripts/make_report.py eval/results/<v2>.json --baseline eval/results/<v1>.json
```

Results are saved with the model, prompt version, prompt hash and git commit, and
`eval/REPORT.md` shows the comparison. See that file for the current numbers.

The prompts were tuned against `golden.yaml`, so scoring well on it is expected. The
real test is a second set written after prompt v3 was frozen:

- `eval/unseen.yaml`: 20 questions based on standard people-analytics KPIs (each tagged
  with its public source: AIHR, Oracle HCM, Keka) and 6 questions worded 3-4 ways each,
  including Swedish. A paraphrase group only counts if every wording passes.
- `eval/injections.yaml`: 20 real prompt-injection attempts sampled from the public
  `deepset/prompt-injections` dataset (Apache-2.0) by `scripts/fetch_injections.py`.

A third set, `eval/adversarial.yaml`, holds 20 out-of-the-box tests run by hand
(authority claims, SQL injection, injected numbers, layoffs, follow-ups, other
languages). They found 12 issues, none of them data leaks; see
`eval/FINDINGS_adversarial.md`. The fixes are prompt v4 plus code checks in
`policy.py`, and the 20 tests now run as regression tests:

```bash
python scripts/run_eval.py --golden eval/adversarial.yaml
```

```bash
python scripts/fetch_injections.py
python scripts/run_eval.py --prompt-version v3 --golden eval/unseen.yaml eval/injections.yaml
python scripts/make_report.py eval/results/<that run>.json --out eval/REPORT_unseen.md
```

## Web app

- `src/workforce/api.py`: FastAPI back end. `GET /api/personas`, `POST /api/ask`,
  `POST /api/feedback`. Per-visitor and daily rate limits protect the free LLM tier, and
  every question is logged as one JSON line.
- `web/`: Next.js front end. Pick a demo leader, ask a question, and see the answer with
  the checks that ran before it was shown, the data table and the SQL.
- The demo uses personas instead of login. A real deployment would map the signed-in
  user (for example through Entra ID single sign-on) to the unit they lead.

Run locally:

```bash
pip install -e ".[api,llm]"
uvicorn workforce.api:app --reload --port 8000 --env-file .env   # back end
cd web && cp .env.example .env.local && npm install && npm run dev   # front end on :3000
```

## Prompt versions

`prompts.py` keeps every version: v1 (Day 3), v2 and v3 (fixes from the first evals) and
v4 (fixes from the adversarial tests, the default). If v4 can't produce a query that
passes the guard, the question is retried once with v3 (`FALLBACK_PROMPT_VERSION`,
`none` to turn off). Each eval result records the version and a hash of its text.

## Known limits

- **Repeated queries.** The differencing rules stop the obvious "everyone minus one
  team" attack. Someone combining many different filters can still narrow down small
  groups. A production system would add query auditing or noise on top.
- **Indicator counts.** `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` inside a group of 5+ can
  still count a very narrow subgroup. The guard blocks using `employee_id` or
  `manager_id` in such conditions, but not every narrow combination of attributes.
- **No conversation memory.** Each question is answered on its own and re-checked against
  the leader's scope. Vague follow-ups are answered with a stated assumption ("I took
  this to mean: ..."). Carrying context would be nicer to use but needs care so a
  follow-up can't widen the scope.
- **Policy checks are pattern-based.** Requests to pick people to cut, recommendations in
  answers, and units are caught by rules in `policy.py`, backed by the v4 prompt. Unusual
  wording can get past a pattern; the adversarial set grows as new cases are found.
- **Synthetic org tree.** Reporting lines are generated, so team-level patterns mean
  nothing about the real dataset.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Download `WA_Fn-UseC_-HR-Employee-Attrition.csv` from
https://www.kaggle.com/datasets/pavansubhasht/ibm-hr-analytics-attrition-dataset
and put it in `data/raw/`.

```bash
python scripts/build_org.py     # builds the org tree, writes data/processed/
python scripts/load_duckdb.py   # loads data/workforce.duckdb
pytest                          # tests use generated fake data, no download needed
```

Ask questions. By default this uses open-weight models (Llama 3.3 70B) on Groq's free
tier through its OpenAI-compatible API, so no paid key is needed. Any OpenAI-compatible
endpoint works, and Claude works with `LLM_PROVIDER=anthropic`.

```bash
pip install -e ".[llm]"
cp .env.example .env            # add your Groq key as OPENAI_API_KEY
python scripts/ask.py --list-leaders
python scripts/ask.py --leader 374 "What is the attrition rate by job role?"
python scripts/smoke_test.py    # 6 scored questions + 1 informational
python scripts/run_eval.py       # 40-question golden set, see Evaluation
```

Optional, Snowflake:

```bash
pip install -e ".[snowflake]"
cp .env.example .env            # fill in your account details
python scripts/load_snowflake.py
```
