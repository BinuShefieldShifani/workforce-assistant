"""Generate a synthetic org hierarchy on top of the IBM HR data.

The Kaggle data has departments and job roles but no reporting lines, and
Workforce Analyst's whole point is that each leader asks about *their own*
organisation. So we build a tree:

    Company (CEO)
      Department (department head)          level 1
        Function = one job role in a dept   level 2
          Team                              level 3

Every person belongs to exactly one unit. A unit's head belongs to the unit
they head. Heads report to the head of the parent unit, and team members
report to their team lead.

We also build a closure table (every ancestor/descendant pair of units).
That makes "everyone under leader X" a single join, which the access layer
on Day 2 depends on.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from workforce.config import MAX_TEAM_SIZE, MIN_TEAM_SIZE, SEED


@dataclass
class OrgData:
    employees: pd.DataFrame
    org_units: pd.DataFrame
    org_closure: pd.DataFrame


def _rank_by_seniority(df: pd.DataFrame) -> pd.DataFrame:
    """Most senior first: job level, then total years worked, then id as a tie-break."""
    return df.sort_values(
        ["job_level", "total_working_years", "employee_id"],
        ascending=[False, False, True],
    )


def build_org(
    raw: pd.DataFrame,
    seed: int = SEED,
    min_team: int = MIN_TEAM_SIZE,
    max_team: int = MAX_TEAM_SIZE,
) -> OrgData:
    """Build employees (with manager_id and org_unit_id), org_units and org_closure."""
    rng = random.Random(seed)
    emp = raw.copy().set_index("employee_id", drop=False)
    emp.index.name = None  # keep employee_id unambiguous as a column
    emp["manager_id"] = pd.Series(pd.NA, index=emp.index, dtype="Int64")
    emp["org_unit_id"] = pd.Series(pd.NA, index=emp.index, dtype="string")

    units: list[dict] = []

    def new_unit(name: str, unit_type: str, parent: str | None, level: int, head: int) -> str:
        unit_id = f"U{len(units) + 1:04d}"
        units.append(
            {
                "org_unit_id": unit_id,
                "name": name,
                "unit_type": unit_type,
                "parent_unit_id": parent,
                "level": level,
                "head_employee_id": int(head),
            }
        )
        return unit_id

    def place(emp_id: int, unit_id: str, manager_id: int | None) -> None:
        emp.at[emp_id, "org_unit_id"] = unit_id
        emp.at[emp_id, "manager_id"] = pd.NA if manager_id is None else int(manager_id)

    ranked = _rank_by_seniority(emp)

    # Level 0: the most senior person overall is the CEO
    ceo = int(ranked.iloc[0]["employee_id"])
    root = new_unit("Company", "company", None, 0, ceo)
    place(ceo, root, None)

    for dept in sorted(emp["department"].unique()):
        members = ranked[(ranked["department"] == dept) & (ranked["employee_id"] != ceo)]
        if members.empty:
            continue

        # Level 1: department, headed by its most senior person
        dept_head = int(members.iloc[0]["employee_id"])
        dept_unit = new_unit(dept, "department", root, 1, dept_head)
        place(dept_head, dept_unit, ceo)
        rest = members.iloc[1:]

        for role in sorted(rest["job_role"].unique()):
            group = rest[rest["job_role"] == role]  # still in seniority order

            # Level 2: one function per job role in the department
            f_head = int(group.iloc[0]["employee_id"])
            f_unit = new_unit(f"{dept} / {role}", "function", dept_unit, 2, f_head)
            place(f_head, f_unit, dept_head)

            ids = [int(i) for i in group.iloc[1:]["employee_id"]]
            if len(ids) < min_team:
                # Too few for a team: they report straight to the function head
                for i in ids:
                    place(i, f_unit, f_head)
                continue

            # Level 3: shuffle and cut into teams of random size
            rng.shuffle(ids)
            pos, team_no = 0, 1
            while pos < len(ids):
                size = rng.randint(min_team, max_team)
                if len(ids) - (pos + size) < min_team:
                    size = len(ids) - pos  # fold a tiny leftover into this team
                chunk = ids[pos : pos + size]
                lead = int(_rank_by_seniority(emp.loc[chunk]).iloc[0]["employee_id"])
                t_unit = new_unit(f"{dept} / {role} / Team {team_no}", "team", f_unit, 3, lead)
                for i in chunk:
                    place(i, t_unit, f_head if i == lead else lead)
                pos += size
                team_no += 1

    org_units = pd.DataFrame(units)
    org_units["parent_unit_id"] = org_units["parent_unit_id"].astype("string")
    employees = emp.reset_index(drop=True).sort_values("employee_id").reset_index(drop=True)
    return OrgData(employees, org_units, build_closure(org_units))


def build_closure(org_units: pd.DataFrame) -> pd.DataFrame:
    """Every (ancestor, descendant, depth) pair, including each unit with itself at depth 0."""
    parent = dict(zip(org_units["org_unit_id"], org_units["parent_unit_id"], strict=True))
    rows = []
    for unit in org_units["org_unit_id"]:
        current, depth = unit, 0
        while current is not None and not pd.isna(current):
            rows.append(
                {"ancestor_unit_id": current, "descendant_unit_id": unit, "depth": depth}
            )
            current, depth = parent[current], depth + 1
            if depth > len(parent):
                raise ValueError(f"Cycle in org_units starting at {unit}")
    return pd.DataFrame(rows)


def validate_org(org: OrgData) -> list[str]:
    """Return a list of problems. An empty list means the org is consistent."""
    errors: list[str] = []
    emp, units, closure = org.employees, org.org_units, org.org_closure

    ids = set(emp["employee_id"])
    if len(ids) != len(emp):
        errors.append("employee_id is not unique")

    roots = units[units["parent_unit_id"].isna()]
    if len(roots) != 1:
        errors.append(f"expected 1 root unit, found {len(roots)}")
        return errors
    root_id = roots.iloc[0]["org_unit_id"]
    ceo = int(roots.iloc[0]["head_employee_id"])

    no_manager = emp[emp["manager_id"].isna()]
    if list(no_manager["employee_id"]) != [ceo]:
        found = list(no_manager["employee_id"])
        errors.append(f"only the CEO should lack a manager, found {found}")

    bad_mgr = set(emp["manager_id"].dropna().astype(int)) - ids
    if bad_mgr:
        errors.append(f"manager_id points to unknown employees: {sorted(bad_mgr)[:5]}")

    unit_ids = set(units["org_unit_id"])
    if emp["org_unit_id"].isna().any():
        errors.append("some employees have no org_unit_id")
    unknown_units = set(emp["org_unit_id"].dropna()) - unit_ids
    if unknown_units:
        errors.append(f"employees reference unknown units: {sorted(unknown_units)[:5]}")
    bad_parents = set(units["parent_unit_id"].dropna()) - unit_ids
    if bad_parents:
        errors.append(f"units reference unknown parents: {sorted(bad_parents)[:5]}")
    if errors:
        return errors

    # Reporting chains must reach the CEO without looping
    manager = dict(zip(emp["employee_id"], emp["manager_id"], strict=True))
    for e in emp["employee_id"]:
        current, steps = e, 0
        while not pd.isna(manager[current]):
            current = int(manager[current])
            steps += 1
            if steps > len(manager):
                errors.append(f"reporting cycle involving employee {e}")
                break
        else:
            if current != ceo:
                errors.append(f"employee {e} does not roll up to the CEO")

    # Unit heads sit in their own unit; reporting lines match the unit tree
    unit_of = dict(zip(emp["employee_id"], emp["org_unit_id"], strict=True))
    head_of = dict(zip(units["org_unit_id"], units["head_employee_id"], strict=True))
    parent_of = dict(zip(units["org_unit_id"], units["parent_unit_id"], strict=True))
    heads = set(head_of.values())
    for unit_id, head in head_of.items():
        if unit_of.get(head) != unit_id:
            errors.append(f"head {head} of {unit_id} is not placed in that unit")
    for e in emp["employee_id"]:
        if e == ceo:
            continue
        u = unit_of[e]
        expected = head_of[parent_of[u]] if e in heads else head_of[u]
        if manager[e] != expected:
            errors.append(f"employee {e} reports to {manager[e]}, expected {expected}")

    # Closure: every unit appears under the root, and with itself at depth 0
    under_root = set(closure.loc[closure["ancestor_unit_id"] == root_id, "descendant_unit_id"])
    if under_root != unit_ids:
        errors.append("closure table: root does not cover every unit")
    self_rows = closure[closure["depth"] == 0]
    if set(self_rows["ancestor_unit_id"]) != unit_ids or not (
        self_rows["ancestor_unit_id"] == self_rows["descendant_unit_id"]
    ).all():
        errors.append("closure table: missing or wrong self rows")

    return errors


def headcount_by_unit(org: OrgData) -> pd.Series:
    """Headcount of each unit including everything below it."""
    joined = org.org_closure.merge(
        org.employees[["employee_id", "org_unit_id"]],
        left_on="descendant_unit_id",
        right_on="org_unit_id",
    )
    return joined.groupby("ancestor_unit_id")["employee_id"].count()


def write_processed(org: OrgData, out_dir: Path) -> dict[str, Path]:
    """Write the three tables as CSV and return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("employees", "org_units", "org_closure"):
        path = out_dir / f"{name}.csv"
        getattr(org, name).to_csv(path, index=False)
        paths[name] = path
    return paths
