"""Step 1 — Build the base project dataset.

Scrapes the two GOV.UK "Levelling Up Fund successful bidders" publications,
parses their attached OpenDocument spreadsheets (.ods), filters to North West
England local authorities, and writes a clean table to
``data/projects_base.csv`` with columns:

    council, project_name, region, round, amount_gbp, source_url

Why this is not a one-line ``pd.read_csv``:
  * GOV.UK publishes these lists as .ods attachments, not CSVs.
  * Round 1 and Round 2 use *different* column headers and layouts.
  * Neither list contains an England-region column, so North West membership
    has to be derived from an explicit local-authority -> county map.

Run directly to (re)generate the dataset:

    python -m pipeline.scrape_base
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Paths and source definitions
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_CSV = DATA_DIR / "projects_base.csv"

# Each round: the human-facing GOV.UK publication page (used as ``source_url``),
# the .ods attachment we actually parse, and the column names in that sheet.
ROUND_SOURCES = {
    1: {
        "page_url": (
            "https://www.gov.uk/government/publications/"
            "levelling-up-fund-first-round-successful-bidders"
        ),
        "ods_url": (
            "https://assets.publishing.service.gov.uk/media/"
            "61967c148fa8f50379269cc9/LUF_bidders_list.ods"
        ),
        "header_row": 1,  # 0-indexed; row 0 is blank
        "council_col": "Local Authority Name / Area",
        "project_col": "Bid Name",
        "amount_col": "Bid Value",
        "country_col": None,  # Round 1 sheet has no country column
    },
    2: {
        "page_url": (
            "https://www.gov.uk/government/publications/"
            "levelling-up-fund-round-2-successful-bidders"
        ),
        "ods_url": (
            "https://assets.publishing.service.gov.uk/media/"
            "646b87038a7184000cae4ef2/LUF_R2_list_of_successful_bids.ods"
        ),
        "header_row": 1,
        "council_col": "Legal name of lead applicant organisation",
        "project_col": "Bid Name",
        "amount_col": "Bid Value",
        "country_col": "Country",  # England / Scotland / Wales / Northern Ireland
    },
}

# --------------------------------------------------------------------------- #
# North West England local authorities
# --------------------------------------------------------------------------- #
# Ceremonial counties of the North West region: Cheshire, Cumbria, Greater
# Manchester, Lancashire, Merseyside. Keys are the *normalised* authority core
# name (see ``normalize_council``); values are the sub-region we record as
# ``region``. Matching is exact-on-normalised-name so that lookalikes such as
# "Wyre Forest" (Worcestershire) are NOT mistaken for "Wyre" (Lancashire).

NW_COUNCILS = {
    # Greater Manchester
    "Bolton": "Greater Manchester",
    "Bury": "Greater Manchester",
    "Manchester": "Greater Manchester",
    "Oldham": "Greater Manchester",
    "Rochdale": "Greater Manchester",
    "Salford": "Greater Manchester",
    "Stockport": "Greater Manchester",
    "Tameside": "Greater Manchester",
    "Trafford": "Greater Manchester",
    "Wigan": "Greater Manchester",
    # Merseyside
    "Knowsley": "Merseyside",
    "Liverpool": "Merseyside",
    "Sefton": "Merseyside",
    "St Helens": "Merseyside",
    "Wirral": "Merseyside",
    # Cheshire (Halton and Warrington are ceremonial Cheshire unitaries)
    "Cheshire East": "Cheshire",
    "Cheshire West and Chester": "Cheshire",
    "Halton": "Cheshire",
    "Warrington": "Cheshire",
    # Lancashire
    "Blackburn with Darwen": "Lancashire",
    "Blackpool": "Lancashire",
    "Burnley": "Lancashire",
    "Chorley": "Lancashire",
    "Fylde": "Lancashire",
    "Hyndburn": "Lancashire",
    "Lancaster": "Lancashire",
    "Pendle": "Lancashire",
    "Preston": "Lancashire",
    "Ribble Valley": "Lancashire",
    "Rossendale": "Lancashire",
    "South Ribble": "Lancashire",
    "West Lancashire": "Lancashire",
    "Wyre": "Lancashire",
    "Lancashire": "Lancashire",  # Lancashire County Council
    # Cumbria (as constituted at award time)
    "Allerdale": "Cumbria",
    "Barrow-in-Furness": "Cumbria",
    "Carlisle": "Cumbria",
    "Copeland": "Cumbria",
    "Eden": "Cumbria",
    "South Lakeland": "Cumbria",
    "Cumbria": "Cumbria",  # Cumbria County Council
}

# "Liverpool City Region" is a combined authority; normalise it onto Liverpool.
COMBINED_AUTHORITY_ALIASES = {
    "Liverpool City Region": "Liverpool",
}

# --------------------------------------------------------------------------- #
# Council-name normalisation and region lookup
# --------------------------------------------------------------------------- #

# Suffixes/prefixes that describe the *type* of authority rather than its place.
_SUFFIX_RE = re.compile(
    r"\s+(?:Metropolitan Borough Council|Borough Council|District Council|"
    r"City Council|County Council|Combined Authority|City Region|Borough|"
    r"Council)$",
    flags=re.IGNORECASE,
)
_PREFIX_RE = re.compile(
    r"^(?:The\s+|(?:London )?Borough of\s+|City of\s+)", flags=re.IGNORECASE
)


def normalize_council(name: str) -> str:
    """Reduce a raw authority name to its bare place name for exact matching.

    Examples:
        "Allerdale Borough Council"        -> "Allerdale"
        "Cheshire West and Chester Council" -> "Cheshire West and Chester"
        "Lancashire County Council*"       -> "Lancashire"
        "Liverpool City Region"            -> "Liverpool"
        "Wyre Forest"                      -> "Wyre Forest"  (stays distinct)
    """
    n = str(name).strip()
    n = n.replace("*", "")               # footnote markers on some Round 2 rows
    n = re.sub(r"\(.*?\)", "", n)        # drop parentheticals
    n = n.strip().strip(",").strip()
    n = _PREFIX_RE.sub("", n)
    n = _SUFFIX_RE.sub("", n).strip()
    return COMBINED_AUTHORITY_ALIASES.get(n, n)


def lookup_region(name: str) -> str | None:
    """Return the North West county for an authority, or None if not NW."""
    return NW_COUNCILS.get(normalize_council(name))


# --------------------------------------------------------------------------- #
# Download + parse
# --------------------------------------------------------------------------- #

def download_source(round_no: int, force: bool = False) -> Path:
    """Download a round's .ods attachment into ``data/raw`` (cached)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / f"luf_r{round_no}.ods"
    if dest.exists() and not force:
        return dest
    url = ROUND_SOURCES[round_no]["ods_url"]
    # A browser-like User-Agent avoids occasional gov.uk asset blocking.
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def parse_round(round_no: int, force_download: bool = False) -> pd.DataFrame:
    """Parse one round's sheet into the standard schema, filtered to NW England."""
    cfg = ROUND_SOURCES[round_no]
    ods_path = download_source(round_no, force=force_download)

    raw = pd.read_excel(ods_path, engine="odf", header=cfg["header_row"])
    raw = raw.dropna(how="all")
    # Drop empty spacer columns pandas names "Unnamed: N".
    raw = raw.loc[:, ~raw.columns.astype(str).str.startswith("Unnamed")]

    # Round 2 carries a country column; keep England only before region lookup.
    if cfg["country_col"] and cfg["country_col"] in raw.columns:
        raw = raw[raw[cfg["country_col"]].astype(str).str.strip() == "England"]

    records = []
    for _, row in raw.iterrows():
        council_raw = row.get(cfg["council_col"])
        project = row.get(cfg["project_col"])
        amount = row.get(cfg["amount_col"])
        if pd.isna(council_raw) or pd.isna(project):
            continue

        region = lookup_region(council_raw)
        if region is None:
            continue  # not a North West England authority

        records.append(
            {
                "council": re.sub(r"\s+", " ", str(council_raw).replace("*", "")).strip(),
                "project_name": str(project).strip(),
                "region": region,
                "round": round_no,
                "amount_gbp": int(amount) if pd.notna(amount) else pd.NA,
                "source_url": cfg["page_url"],
            }
        )

    return pd.DataFrame.from_records(records)


def build_base_dataset(force_download: bool = False) -> pd.DataFrame:
    """Build and persist the North West base dataset for both rounds."""
    frames = [parse_round(r, force_download=force_download) for r in ROUND_SOURCES]
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["region", "council", "round"]).reset_index(drop=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)
    return df


def main() -> None:
    df = build_base_dataset()
    total = df["amount_gbp"].sum()
    print(f"Wrote {len(df)} North West projects to {OUTPUT_CSV.relative_to(REPO_ROOT)}")
    print(f"Rounds: {df['round'].value_counts().to_dict()}")
    print(f"By region: {df['region'].value_counts().to_dict()}")
    print(f"Total funding: £{total:,.0f}")
    print()
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(df.to_string(index=False, max_colwidth=45))


if __name__ == "__main__":
    main()
