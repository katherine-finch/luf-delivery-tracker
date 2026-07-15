"""One-off -- write a short, neutral "original plan" description for each project.

Unlike delivery *status* (which changes and is refreshed weekly by the agent), a
project's description of what it set out to deliver is essentially static. So we
generate it **once**, cache it in ``data/descriptions.csv``, commit it, and the
weekly pipeline never re-derives it. This mirrors how ``locations.csv`` is cached.

To avoid fabrication, each description is grounded in real retrieved text: we run
one web search per project (favouring the lead council's own pages) and ask the
model to summarise ONLY what those results say -- no invented figures or scope.

Run:  python -m pipeline.describe          # fills in only missing rows
      python -m pipeline.describe --force  # regenerate every description
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from dotenv import load_dotenv

from .agent import _build_chat_model
from .retrieve import get_client as _get_tavily_client

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
BASE_CSV = DATA_DIR / "projects_base.csv"
DESCRIPTIONS_CSV = DATA_DIR / "descriptions.csv"

_OUTPUT_COLUMNS = ["project_name", "summary"]
_MAX_RESULTS = 5

_SYSTEM = (
    "You are a neutral public-policy writer. From the provided search results, "
    "write ONE or TWO plain sentences (max 45 words) describing what this UK "
    "Levelling Up Fund project is and what it originally set out to deliver -- "
    "e.g. what is being built or renovated and its headline aim. Prefer the lead "
    "council's own description. Use ONLY facts present in the results; do not "
    "invent figures, dates, or scope. Do NOT comment on delivery progress or "
    "whether it is on time. Reply with the description text only -- no preamble."
)


class DescribeError(RuntimeError):
    """Raised when inputs are missing or the model/search backend is unavailable."""


def _read_projects() -> list[dict]:
    if not BASE_CSV.exists():
        raise DescribeError(f"Missing {BASE_CSV.name}. Run pipeline.scrape_base first.")
    with BASE_CSV.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_cache() -> dict[str, str]:
    if not DESCRIPTIONS_CSV.exists():
        return {}
    with DESCRIPTIONS_CSV.open(newline="", encoding="utf-8") as fh:
        return {r["project_name"]: r.get("summary", "") for r in csv.DictReader(fh)}


def _search_context(tavily, project_name: str, council: str) -> str:
    """Return a numbered block of search snippets to ground the description."""
    query = f"{project_name} {council} Levelling Up Fund project plan"
    try:
        response = tavily.search(query=query, max_results=_MAX_RESULTS, search_depth="basic")
    except Exception as exc:  # pragma: no cover - network guard
        raise DescribeError(f"Search failed for {project_name!r}: {exc}") from exc
    results = response.get("results", [])
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"[{i}] {r.get('title', '').strip()}\n{r.get('content', '').strip()}")
    return "\n\n".join(lines)


def build_descriptions(force: bool = False) -> list[dict]:
    """Generate (or reuse cached) descriptions for every project; writes the CSV."""
    projects = _read_projects()
    cache = {} if force else _read_cache()

    model = _build_chat_model()
    tavily = _get_tavily_client()
    rows: list[dict] = []

    for p in projects:
        name = p["project_name"]
        if cache.get(name):
            rows.append({"project_name": name, "summary": cache[name]})
            continue

        context = _search_context(tavily, name, p["council"])
        prompt = (
            f"PROJECT: {name} (lead body: {p['council']})\n\n"
            f"SEARCH RESULTS:\n{context}\n\n"
            "Write the description now."
        )
        reply = model.invoke(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}]
        )
        summary = str(getattr(reply, "content", reply)).strip()
        print(f"  described {name!r}: {summary[:70]}...")
        rows.append({"project_name": name, "summary": summary})

    with DESCRIPTIONS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate one-off project descriptions.")
    parser.add_argument(
        "--force", action="store_true", help="Regenerate every description, ignoring the cache."
    )
    args = parser.parse_args()

    rows = build_descriptions(force=args.force)
    print(f"\nWrote {len(rows)} description(s) to {DESCRIPTIONS_CSV}")


if __name__ == "__main__":
    main()
