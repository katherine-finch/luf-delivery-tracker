"""Step 5a -- attach a map location to every project.

The dashboard is a *slippy map*, so each project needs a latitude/longitude to be
placed. We don't hand-enter coordinates: we curate a human-readable ``place`` for
each project (the town/site it actually sits in) and geocode it **once** with the
free OpenStreetMap Nominatim service. The result is cached in
``data/locations.csv`` and committed, so the weekly refresh never re-geocodes and
the dashboard has no runtime dependency on any geocoder.

Some LUF awards are county-wide (e.g. "Levelling Up East Lancashire") and have no
single site. Those are geocoded to the area's main town and flagged
``area_wide=True`` so the frontend can render them differently (an area, not a
precise point).

Run:  python -m pipeline.geocode          # fills in only missing rows
      python -m pipeline.geocode --force  # re-geocode everything

Data © OpenStreetMap contributors, ODbL. Nominatim usage policy: max 1 request/
second and a descriptive User-Agent -- both honoured below.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
BASE_CSV = DATA_DIR / "projects_base.csv"
LOCATIONS_CSV = DATA_DIR / "locations.csv"

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim asks for a genuine, contactable identifier. Keep it descriptive.
_USER_AGENT = "luf-delivery-tracker/1.0 (portfolio project; contact via GitHub)"
_REQUEST_PAUSE_S = 1.1  # stay under the 1 req/sec policy

_OUTPUT_COLUMNS = ["project_name", "place", "lat", "lon", "area_wide"]

# Curated map: project_name -> (place query, area_wide?).
# The place is the actual town/site of the works -- NOT always the council name
# (e.g. Eden Project North is on Morecambe seafront; Woodside is in Birkenhead).
# ", UK" is appended at query time to disambiguate. Edit a `place` here and re-run
# with --force to move a pin.
_PLACES: dict[str, tuple[str, bool]] = {
    "Transforming Ellesmere Port Town Centre": ("Ellesmere Port", False),
    "Workington Gateway": ("Workington", False),
    "Barrow-in-Furness Town Centre": ("Barrow-in-Furness", False),
    "Industrial Solutions Hub (iSH) Enterprise Campus": ("Whitehaven, Cumbria", False),
    "Energy Coast Resilient Routes": ("Workington, Cumbria", True),
    "Bolton College of Medical Science (Development)": ("Bolton", False),
    "Radcliffe (Civic and Enterprise Hub Development)": ("Radcliffe, Greater Manchester", False),
    "BuryMarket FlexiHall": ("Bury, Greater Manchester", False),
    "The Culture in the City project": ("Manchester", False),
    "Green Technology and Innovation Network": ("Oldham", False),
    "Salford Rise (Innovation Zone)": ("Salford", False),
    "Ashton (Town Centre Regeneration)": ("Ashton-under-Lyne", False),
    "The Redevelopment of Partington Sports Village": ("Partington, Greater Manchester", False),
    "Haigh Hall": ("Haigh Hall, Wigan", False),
    "Blackburn Growth Axis Transport Package (Southeast)": ("Blackburn", False),
    "Multiversity": ("Blackpool", False),
    "Burnley Campus Expansion;  Turf Public Realm Transformation; "
    "Railway Station Accessibility Improvements (package)": ("Burnley", False),
    "The Accrington Acre: Our Heritage-Led Town Centre": ("Accrington", False),
    "Levelling Up East Lancashire: Creating opportunities through greener, "
    "safer and healthier travel": ("Burnley, Lancashire", True),
    "Eden Project North": ("Morecambe", False),
    "Colne Town Centre (Investment)": ("Colne, Lancashire", False),
    "Active Preston: Transforming Our Community Infrastructure": ("Preston", False),
    "Halewood Leisure and Connectivity Improvements": ("Halewood, Merseyside", False),
    "Liverpool City Council Docks Cultural Regeneration": ("Royal Albert Dock, Liverpool", False),
    "Levelling Up for Recovery (Transport Infrastructure Improvements)": ("Liverpool", True),
    "Earlestown: Regeneration through Culture, Community and Heritage": (
        "Earlestown, Merseyside",
        False,
    ),
    "Woodside (Woodside WaterFront Visitor and Gyratory Reconfiguration)": (
        "Woodside, Birkenhead",
        False,
    ),
}


class GeocodeError(RuntimeError):
    """Raised when a project has no curated place or geocoding fails."""


def _read_base_project_names() -> list[str]:
    with BASE_CSV.open(newline="", encoding="utf-8") as fh:
        return [row["project_name"] for row in csv.DictReader(fh)]


def _read_cache() -> dict[str, dict]:
    """Return existing locations keyed by project_name (empty if no cache yet)."""
    if not LOCATIONS_CSV.exists():
        return {}
    with LOCATIONS_CSV.open(newline="", encoding="utf-8") as fh:
        return {row["project_name"]: row for row in csv.DictReader(fh)}


def _geocode(place: str) -> tuple[float, float]:
    """Look up one place via Nominatim, returning (lat, lon).

    Tries the full query first, then progressively simpler variants (dropping a
    trailing county qualifier, then the bare town) so an over-specific query like
    "Whitehaven, Cumbria" still resolves.
    """
    # Build candidate queries, most specific first, de-duplicated in order.
    head = place.split(",")[0].strip()
    candidates = []
    for q in (f"{place}, UK", place, f"{head}, UK", head):
        if q not in candidates:
            candidates.append(q)

    for i, query in enumerate(candidates):
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "gb"},
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
        if i < len(candidates) - 1:
            time.sleep(_REQUEST_PAUSE_S)  # pause before the next attempt

    raise GeocodeError(f"No geocoding result for {place!r}")


def build_locations(force: bool = False) -> list[dict]:
    """Geocode every project (or only those missing from the cache).

    Returns the full list of location rows and writes them to locations.csv.
    """
    project_names = _read_base_project_names()
    cache = {} if force else _read_cache()
    rows: list[dict] = []

    for name in project_names:
        if name in cache and cache[name].get("lat") and cache[name].get("lon"):
            rows.append(cache[name])  # already geocoded -> reuse, no network call
            continue

        if name not in _PLACES:
            raise GeocodeError(
                f"No curated place for {name!r}. Add it to _PLACES in pipeline/geocode.py."
            )
        place, area_wide = _PLACES[name]
        lat, lon = _geocode(place)
        print(f"  geocoded {name!r} -> {place} ({lat:.4f}, {lon:.4f})")
        rows.append(
            {
                "project_name": name,
                "place": place,
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "area_wide": "true" if area_wide else "false",
            }
        )
        time.sleep(_REQUEST_PAUSE_S)  # respect Nominatim rate limit

    with LOCATIONS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode LUF projects for the map.")
    parser.add_argument(
        "--force", action="store_true", help="Re-geocode every project, ignoring the cache."
    )
    args = parser.parse_args()

    rows = build_locations(force=args.force)
    print(f"\nWrote {len(rows)} location(s) to {LOCATIONS_CSV}")


if __name__ == "__main__":
    main()
