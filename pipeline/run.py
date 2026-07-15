"""Step 3 runner -- classify every project into data/predictions.csv.

Usage:
    python -m pipeline.run                # classify all projects
    python -m pipeline.run --limit 3      # quick/cheap smoke test on 3 rows
    python -m pipeline.run --only "Salford Rise (Innovation Zone)"

Reads data/projects_base.csv, runs the LangGraph ReAct agent (agent.py) on each
project -- the agent searches the web in a loop until it can justify a status --
and writes data/predictions.csv. Output columns line up with data/ground_truth.csv
so Step 4 can join and score them directly.
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


def run(limit: int | None = None, only: str | None = None) -> Path:
    """Classify projects from the base dataset and write predictions.csv."""
    base = pd.read_csv(BASE_CSV)

    if only:
        base = base[base["project_name"] == only]
        if base.empty:
            raise SystemExit(f"No project named {only!r} in {BASE_CSV.name}")
    if limit:
        base = base.head(limit)

    rows = []
    total = len(base)
    for n, (_, project) in enumerate(base.iterrows(), start=1):
        name = project["project_name"]
        council = project["council"]
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
    args = parser.parse_args()
    run(limit=args.limit, only=args.only)


if __name__ == "__main__":
    main()
