"""Step 5b -- assemble the dashboard's data file.

Joins the three published CSVs into one JSON blob the static map reads directly:

    projects_base.csv   -> council, amount_gbp, round, source_url (the award)
    predictions.csv     -> status, confidence, justification, citations (the analysis)
    locations.csv       -> lat, lon, place, area_wide (where the pin sits)

The dashboard is deliberately *dumb*: it loads this file and renders it. It never
calls the LLM, Tavily, or a geocoder at page-load, so it is fast, free to host,
and exposes no API keys. Re-run this whenever predictions.csv changes.

Run:  python -m pipeline.build_dashboard
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
BASE_CSV = DATA_DIR / "projects_base.csv"
PREDICTIONS_CSV = DATA_DIR / "predictions.csv"
LOCATIONS_CSV = DATA_DIR / "locations.csv"
DESCRIPTIONS_CSV = DATA_DIR / "descriptions.csv"
OUTPUT_JSON = REPO_ROOT / "dashboard" / "data.json"


class BuildError(RuntimeError):
    """Raised when an input file is missing or the join loses projects."""


def _require(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise BuildError(
            f"Missing {path.name}. Run the pipeline first "
            "(scrape_base -> run -> geocode)."
        )
    return pd.read_csv(path)


def _parse_citations(raw) -> list:
    """predictions.csv stores citations as a JSON string; decode defensively."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def build() -> dict:
    """Join the CSVs and return the dashboard payload (also written to disk)."""
    base = _require(BASE_CSV)
    locations = _require(LOCATIONS_CSV)

    # Predictions may not exist yet (pipeline not run) -- degrade gracefully so the
    # map still renders every award as "unknown" rather than failing to build.
    if PREDICTIONS_CSV.exists():
        preds = pd.read_csv(PREDICTIONS_CSV)
    else:
        preds = pd.DataFrame(
            columns=["project_name", "status", "confidence", "justification", "citations"]
        )

    # Static one-off project descriptions (the "original plan"). Optional: the
    # map still builds without them, just with no plan text in the panel.
    if DESCRIPTIONS_CSV.exists():
        descriptions = pd.read_csv(DESCRIPTIONS_CSV)[["project_name", "summary"]]
    else:
        descriptions = pd.DataFrame(columns=["project_name", "summary"])

    merged = base.merge(locations, on="project_name", how="left")
    missing_loc = merged[merged["lat"].isna()]["project_name"].tolist()
    if missing_loc:
        raise BuildError(
            f"{len(missing_loc)} project(s) have no location: {missing_loc}. "
            "Run: python -m pipeline.geocode"
        )

    merged = merged.merge(
        preds[["project_name", "status", "confidence", "justification", "citations"]],
        on="project_name",
        how="left",
    )
    merged = merged.merge(descriptions, on="project_name", how="left")

    projects = []
    for _, r in merged.iterrows():
        status = r.get("status")
        status = status if isinstance(status, str) and status else "unknown"
        confidence = r.get("confidence")
        confidence = confidence if isinstance(confidence, str) and confidence else None
        justification = r.get("justification")
        justification = justification if isinstance(justification, str) else ""
        summary = r.get("summary")
        summary = summary if isinstance(summary, str) else ""

        projects.append(
            {
                "project_name": r["project_name"],
                "council": r["council"],
                "region": r.get("region"),
                "round": int(r["round"]) if pd.notna(r.get("round")) else None,
                "amount_gbp": int(r["amount_gbp"]) if pd.notna(r.get("amount_gbp")) else None,
                "award_url": r.get("source_url"),
                "place": r.get("place"),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "area_wide": str(r.get("area_wide")).lower() == "true",
                "summary": summary,
                "status": status,
                "confidence": confidence,
                "justification": justification,
                "citations": _parse_citations(r.get("citations")),
            }
        )

    # Portfolio-level summary for the header stat block.
    total_awarded = int(base["amount_gbp"].sum())
    status_counts: dict[str, int] = {}
    for p in projects:
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1

    payload = {
        "generated": date.today().isoformat(),
        "summary": {
            "project_count": len(projects),
            "total_awarded_gbp": total_awarded,
            "status_counts": status_counts,
            "classified_count": sum(1 for p in projects if p["status"] != "unknown"),
        },
        "projects": projects,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    return payload


def main() -> None:
    payload = build()
    s = payload["summary"]
    print(f"Wrote {OUTPUT_JSON}")
    print(
        f"  {s['project_count']} projects, "
        f"\u00a3{s['total_awarded_gbp']:,} awarded, "
        f"{s['classified_count']} classified"
    )
    print(f"  status counts: {s['status_counts']}")


if __name__ == "__main__":
    main()
