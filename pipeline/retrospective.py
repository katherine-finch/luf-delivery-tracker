"""One-off -- write a short "how it went" retrospective for *completed* projects.

Once a project is finished its delivery status stops changing, so the weekly
agent no longer re-runs it (see pipeline/run.py). What a reader still wants at
that point is not "is it on track?" but "how did it actually turn out?" -- did it
open on time, on budget, and deliver what was promised. This module fills that
gap for completed projects only.

Like descriptions.csv and locations.csv, the output is generated once, cached in
``data/retrospectives.csv``, committed, and never re-derived by the weekly job.
Which projects count as "completed" is read from ``data/predictions.csv``.

To avoid fabrication, each retrospective is grounded in real retrieved text: we
run one web search per project and ask the model to summarise ONLY what those
results say -- no invented figures, dates, or spin.

Run:  python -m pipeline.retrospective          # fills in only missing rows
      python -m pipeline.retrospective --force   # regenerate every retrospective
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from dotenv import load_dotenv

from ._grounded import get_backends, search_context

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
BASE_CSV = DATA_DIR / "projects_base.csv"
PREDICTIONS_CSV = DATA_DIR / "predictions.csv"
RETROSPECTIVES_CSV = DATA_DIR / "retrospectives.csv"

_OUTPUT_COLUMNS = ["project_name", "outcome"]

_SYSTEM = (
    "You are a neutral public-policy writer. This UK Levelling Up Fund project is "
    "now COMPLETE. From the provided search results, write ONE or TWO plain "
    "sentences (max 45 words) describing how the project actually turned out -- "
    "e.g. when it opened, what was delivered, and any noted delays, overspend, or "
    "reception. Use ONLY facts present in the results; do not invent figures, "
    "dates, or outcomes. Do not add promotional spin. If the results do not "
    "describe this specific project's actual delivery or outcome, reply with "
    "exactly this sentence and nothing else: Insufficient publicly available "
    "information. Otherwise reply with the retrospective text only -- no preamble."
)


class RetrospectiveError(RuntimeError):
    """Raised when inputs are missing or the model/search backend is unavailable."""


def _clean_outcome(text: str) -> str:
    """Return the retrospective, or '' when the model reports insufficient evidence.

    The prompt instructs the model to reply with exactly "Insufficient publicly
    available information." when the search results don't describe the project's
    delivery. Matching that one agreed sentence is far more robust than trying to
    enumerate every way a model might phrase a refusal.
    """
    stripped = text.strip()
    if stripped.rstrip(".").strip().lower() == "insufficient publicly available information":
        return ""
    return stripped


def _completed_projects() -> list[dict]:
    """Return base rows for projects predicted 'completed', joined with council."""
    if not BASE_CSV.exists():
        raise RetrospectiveError(f"Missing {BASE_CSV.name}. Run pipeline.scrape_base first.")
    if not PREDICTIONS_CSV.exists():
        raise RetrospectiveError(
            f"Missing {PREDICTIONS_CSV.name}. Run pipeline.run first so we know "
            "which projects are completed."
        )

    with PREDICTIONS_CSV.open(newline="", encoding="utf-8") as fh:
        completed = {
            r["project_name"] for r in csv.DictReader(fh) if r.get("status") == "completed"
        }
    with BASE_CSV.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["project_name"] in completed]


def _read_cache() -> dict[str, str]:
    if not RETROSPECTIVES_CSV.exists():
        return {}
    with RETROSPECTIVES_CSV.open(newline="", encoding="utf-8") as fh:
        return {r["project_name"]: r.get("outcome", "") for r in csv.DictReader(fh)}


def build_retrospectives(force: bool = False) -> list[dict]:
    """Generate (or reuse cached) retrospectives for completed projects; writes CSV."""
    projects = _completed_projects()
    cache = {} if force else _read_cache()

    model, tavily = get_backends()
    rows: list[dict] = []

    for p in projects:
        name = p["project_name"]
        if cache.get(name):
            rows.append({"project_name": name, "outcome": cache[name]})
            continue

        context = search_context(
            tavily, f"{name} {p['council']} completed opened outcome delivered"
        )
        prompt = (
            f"PROJECT: {name} (lead body: {p['council']})\n\n"
            f"SEARCH RESULTS:\n{context}\n\n"
            "Write the retrospective now."
        )
        reply = model.invoke(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]
        )
        outcome = _clean_outcome(str(getattr(reply, "content", reply)))
        shown = outcome[:70] if outcome else "(no grounded outcome -- omitted)"
        print(f"  retrospective {name!r}: {shown}...")
        rows.append({"project_name": name, "outcome": outcome})

    with RETROSPECTIVES_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one-off retrospectives for completed projects."
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate every retrospective, ignoring the cache."
    )
    args = parser.parse_args()

    rows = build_retrospectives(force=args.force)
    print(f"\nWrote {len(rows)} retrospective(s) to {RETROSPECTIVES_CSV}")


if __name__ == "__main__":
    main()
