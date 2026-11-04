"""Project paths and constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_CSV = DATA_DIR / "raw" / "WA_Fn-UseC_-HR-Employee-Attrition.csv"
PROCESSED_DIR = DATA_DIR / "processed"
DUCKDB_PATH = DATA_DIR / "workforce.duckdb"

SEED = 42

# Team sizes are drawn from this range. The lower bound is deliberately below 5
# so the data contains small groups for testing small-group suppression later.
MIN_TEAM_SIZE = 3
MAX_TEAM_SIZE = 12

# Table names, shared by the DuckDB and Snowflake loaders
TABLES = ("employees", "org_units", "org_closure")

# Privacy: never report on a group smaller than this
MIN_GROUP_SIZE = 5
# Cap on rows a single question can return
MAX_RESULT_ROWS = 200
