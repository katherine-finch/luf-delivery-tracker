"""Step 2 runner -- classify every project into data/predictions.csv.

Usage:
    python -m pipeline.run                # classify all projects
    python -m pipeline.run --limit 3      # quick/cheap smoke test on 3 rows
    python -m pipeline.run --only "Salford Rise (Innovation Zone)"

Reads data/projects_base.csv, runs the LangGraph ReAct agent (agent.py) on each
project -- the agent searches the web in a loop until it can justify a status --
and writes data/predictions.csv.

Projects already classified as ``completed`` in a previous run are *locked*: a
finished project's delivery status will not change, so we skip re-running the
(paid) agent on them and carry their existing prediction row through unchanged.
Pass ``--refresh-completed`` to force a re-classification of those too.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd

from .agent import classify_project

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BASE_CSV = DATA_DIR / "projects_base.csv"
OUT_CSV = DATA_DIR / "predictions.csv"

_OUTPUT_COLUMNS = [
    "project_name",
    "council",
    "status",
    "confidence",
    "justification",
    "citations",
    "model",
    "backend",
]


def _load_completed_rows() -> dict[str, dict]:
    """Return {project_name: row} for projects already classified 'completed'.

    These are carried through unchanged so we don't re-run the agent on finished
    projects. Missing/blank columns are filled so the row still writes cleanly.
    """
    if not OUT_CSV.exists():
        return {}
    prior = pd.read_csv(OUT_CSV)
    if "status" not in prior.columns:
        return {}
    done = prior[prior["status"] == "completed"]
    locked: dict[str, dict] = {}
    for _, row in done.iterrows():
        record = {col: row.get(col, "") for col in _OUTPUT_COLUMNS}
        record = {k: ("" if pd.isna(v) else v) for k, v in record.items()}
        locked[record["project_name"]] = record
    return locked


def run(limit: int | None = None, only: str | None = None, refresh_completed: bool = False) -> Path:
    """Classify projects from the base dataset and write predictions.csv."""
    base = pd.read_csv(BASE_CSV)

    if only:
        base = base[base["project_name"] == only]
        if base.empty:
            raise SystemExit(f"No project named {only!r} in {BASE_CSV.name}")
    if limit:
        base = base.head(limit)

    # Projects already marked "completed" are done -- their status will not
    # change, so reuse the previous prediction rather than paying to re-run the
    # agent. --refresh-completed overrides this to re-classify everything.
    locked = {} if refresh_completed else _load_completed_rows()

    rows = []
    total = len(base)
    for n, (_, project) in enumerate(base.iterrows(), start=1):
        name = project["project_name"]
        council = project["council"]

        if name in locked:
            print(f"[{n}/{total}] {name} ({council}) -- locked (completed), reusing prior result")
            rows.append(locked[name])
            continue

        print(f"[{n}/{total}] {name} ({council})")

        result = classify_project(name, council)
        print(f"  -> {result.status} ({result.confidence}), {len(result.citations)} citation(s)")
        rows.append(result.to_row())

    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} prediction(s) to {OUT_CSV}")
    return OUT_CSV


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LUF classification pipeline.")
    parser.add_argument("--limit", type=int, default=None, help="classify only the first N projects")
    parser.add_argument("--only", type=str, default=None, help="classify a single named project")
    parser.add_argument(
        "--refresh-completed",
        action="store_true",
        help="re-classify projects already marked 'completed' (they are locked by default)",
    )
    args = parser.parse_args()
    run(limit=args.limit, only=args.only, refresh_completed=args.refresh_completed)


if __name__ == "__main__":
    main()
