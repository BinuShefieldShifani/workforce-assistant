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
- [ ] Day 2: access scoping and small-group suppression
- [ ] Day 3: text-to-SQL agent with answer verification
- [ ] Day 4: golden question set and eval report
- [ ] Day 5: FastAPI back end, Next.js front end, deployment

## Data model

| Table | What it holds |
|---|---|
| `employees` | One row per person: HR attributes, `manager_id`, `org_unit_id` |
| `org_units` | Company, departments, functions and teams, with `parent_unit_id` and `head_employee_id` |
| `org_closure` | Every ancestor/descendant pair of units, so "everyone under unit X" is one join |
| `unit_headcount` (view) | Headcount per unit including everything below it |
| `leaders` (view) | Everyone who heads a unit, with their headcount |

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

Optional, Snowflake:

```bash
pip install -e ".[snowflake]"
cp .env.example .env            # fill in your account details
python scripts/load_snowflake.py
```
