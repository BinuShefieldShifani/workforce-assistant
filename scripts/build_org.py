"""Day 1, step 1: read the Kaggle CSV, build the synthetic org tree, write data/processed/.

Usage:
    python scripts/build_org.py
    python scripts/build_org.py --input path/to/file.csv --seed 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from workforce.config import MIN_TEAM_SIZE, PROCESSED_DIR, RAW_CSV, SEED
from workforce.org import build_org, headcount_by_unit, validate_org, write_processed
from workforce.raw import EXPECTED_ROWS, load_raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=RAW_CSV)
    parser.add_argument("--out", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    raw = load_raw(args.input)
    print(f"Loaded {len(raw)} employees, {raw.shape[1]} columns from {args.input}")
    if len(raw) != EXPECTED_ROWS:
        print(f"WARNING: expected {EXPECTED_ROWS} rows. Is this the original Kaggle file?")

    org = build_org(raw, seed=args.seed)
    errors = validate_org(org)
    if errors:
        print("Org validation FAILED:")
        for e in errors[:20]:
            print("  -", e)
        return 1

    paths = write_processed(org, args.out)

    units = org.org_units
    heads = headcount_by_unit(org)
    teams = units[units["unit_type"] == "team"]
    team_sizes = heads.loc[teams["org_unit_id"]]
    ceo = int(units.loc[units["parent_unit_id"].isna(), "head_employee_id"].iloc[0])

    print("Org validation passed")
    print(f"CEO: employee {ceo}")
    print("Units by type:", units["unit_type"].value_counts().to_dict())
    print(f"Team size: min {team_sizes.min()}, max {team_sizes.max()}")
    small = (heads < 5).sum()
    print(f"Units with fewer than 5 people (incl. sub-units): {small}  "
          f"(min team size is {MIN_TEAM_SIZE}, so some are expected)")
    for path in paths.values():
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
