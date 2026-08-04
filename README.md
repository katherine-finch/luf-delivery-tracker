# Policy → Delivery: an automated tracker for Levelling Up Fund delivery status

**Scope of this MVP:** This project currently covers **only the Levelling Up
Fund (LUF), and only projects led by North West England local authorities**
(Rounds 1 and 2 — 27 awards). It does **not** yet cover other UK regions, the
Towns Fund, or the UK Shared Prosperity Fund.

---

## The problem

Between 2020-21 and 2025-26 the UK government allocated ~£10.47 billion across
the Levelling Up Fund, Towns Fund, and UK Shared Prosperity Fund to 4,000+ local
projects. Official NAO and government evaluations (2023, and a 2025 evaluation)
found that the large majority of funded projects are delayed — but those are
**one-off manual snapshots**, not a living tracker.

**Goal:** build an *automated, repeatable* pipeline that classifies each
project's real-world delivery status from public text sources, so the picture
can be refreshed on demand rather than reconstructed by hand each time.

Delivery status is drawn from six categories:

`on_track` · `delayed` · `stalled` · `rescoped` · `cancelled` · `completed`

---

## Methodology

| Step | What it does | Status |
|------|--------------|--------|
| **1. Base dataset** | Parse the GOV.UK LUF Round 1 & 2 bidder lists (.ods), filter to North West England, produce a clean table. | ✅ Done |
| **2. Retrieve + classify** | A LangGraph ReAct agent researches each project — searching the web (Tavily) in a loop until it can justify a status with Claude — returning status, confidence, a justification, and multiple `{source, finding}` citations. | ✅ Done |
| **3. Present** | Static, zero-backend map dashboard: every project pinned and colour-coded by status, with a click-through panel showing the original plan, the inferred status, and the cited evidence behind it. | ✅ Done |

---

## Step 1 — base dataset (done)

Source publications (GOV.UK, Open Government Licence v3.0):

- Round 1: <https://www.gov.uk/government/publications/levelling-up-fund-first-round-successful-bidders>
- Round 2: <https://www.gov.uk/government/publications/levelling-up-fund-round-2-successful-bidders>

Both lists are published as **OpenDocument spreadsheets (.ods)**, not CSVs, and
the two rounds use different column layouts. The scraper
([`pipeline/scrape_base.py`](pipeline/scrape_base.py)) downloads and caches each
attachment, normalises the columns, and filters to North West England.

**Region filtering.** Neither list has an England-region column (Round 2 only
has *country*). North West membership is derived from an explicit
local-authority → county map covering the five ceremonial counties (Cheshire,
Cumbria, Greater Manchester, Lancashire, Merseyside). Matching is done on a
*normalised* authority name (suffixes like "Borough Council" stripped) with
**exact** comparison, so lookalikes such as "Wyre Forest" (Worcestershire) are
correctly **not** matched to "Wyre" (Lancashire).

Output: [`data/projects_base.csv`](data/projects_base.csv) —
`council, project_name, region, round, amount_gbp, source_url`.

**Result: 27 projects** (Round 1: 12, Round 2: 15) totalling **£586.5m**.

| Region | Projects |
|--------|---------:|
| Greater Manchester | 9 |
| Lancashire | 8 |
| Merseyside | 5 |
| Cumbria | 4 |
| Cheshire | 1 |

Regenerate at any time with:

```bash
python -m pipeline.scrape_base
```

---

## How delivery status is defined

Before classifying anything, we pin down what "delivered" means and which parts
of it public text can actually reveal.

### How we gauge delivery status

"Delivered well" is fuzzy, so we decompose it into **observable dimensions** and
are explicit about which ones public text can actually reveal:

| Dimension | Question | Visible in public sources? |
|-----------|----------|----------------------------|
| **Progress / momentum** | Is physical work happening? | ✅ Yes — "construction started", "site works ongoing", "opened" |
| **Schedule** | On the *original* timeline? | ⚠️ Partly — needs bid baseline vs current forecast |
| **Scope** | Still delivering what was promised? | ⚠️ Sometimes — "half-sized redesign", "revised scheme" |
| **Budget / value / outcomes** | On budget? Jobs, footfall delivered? | ❌ Rarely — councils don't publish overruns; outcomes take years |

The status label mainly measures **progress** and **scope**, glances at
**schedule**, and deliberately does **not** claim to judge value for money. This
is a **delivery-momentum tracker, not a value-for-money assessment.**

**The core discipline: label on *events*, not *sentiment*.** "Council remains
committed to…" is PR noise; "piling and demolition underway" is an observable
fact. Each status maps to concrete signal patterns:

| Status | Signal in the source text |
|--------|---------------------------|
| `completed` | "opened", "complete", ribbon-cutting / reopening event |
| `on_track` | construction / site works verifiably underway; no adverse scope or schedule news |
| `delayed` | work happening **but** explicit slippage: "extended to 2028", "pushed back" |
| `rescoped` | scope materially changed: "scaled back", "revised smaller scheme", element dropped |
| `stalled` | funded but **no** evidence of physical progress; "paused", "on hold" |
| `cancelled` | "scrapped", "withdrawn", "returned funding" |

Note `on_track` means *delivery is visibly underway*, **not** *on the original
schedule* — a project can be actively building yet already late against its 2021
bid. We label progress, and flag schedule slippage separately as `delayed`.

### Confidence tiering

Every classification carries a `confidence` grade reflecting **evidence
strength**, so a weak judgement is never presented as a strong one:

- **`high`** — a dated, physical event from a primary source (e.g. "construction began Oct 2024").
- **`med`** — credible progress but partly the council's own framing.
- **`low`** — only forward-looking language ("progress *expected* in 2025").

The classifier returns status **+ confidence + a supporting quote** together, so
a reader can always weigh how firm each call is. `stalled` and `cancelled` are
valid categories but rare among these North West awards — they are only applied
where primary evidence supports them, never invented to fill the table.

---

## Limitations & honesty notes

- **North West only.** See the scope banner above.
- A classification reflects the delivery position at the time of the run; a
  "completed" flagship element (e.g. Colne Market Hall) may sit inside a wider
  programme that is still ongoing.
- Every status classification must trace back to a real retrieved source — the
  agent may not guess (enforced in code, see below).

---

## Step 2 — retrieve + classify (done)

The pipeline turns the base dataset into a delivery-status prediction for every
project, using only text it can cite. Classification is done by a **LangGraph
ReAct agent**: Claude is given a `web_search` tool and told to research each
project, looping — reason about what it still doesn't know → issue a more
targeted search → repeat — until it can justify a status or hits a search budget
(`AGENT_MAX_SEARCHES`, default 5). It then returns a verdict citing *every*
source it used.

| Module | Job |
|--------|-----|
| [`pipeline/retrieve.py`](pipeline/retrieve.py) | Tavily search-client factory (centralises API-key handling) used by the agent's `web_search` tool. |
| [`pipeline/agent.py`](pipeline/agent.py) | The ReAct agent: builds the Claude model for the chosen backend, runs the search loop, and parses a structured verdict. |
| [`pipeline/run.py`](pipeline/run.py) | Run the agent over all projects and write `data/predictions.csv`. |

**The no-guessing rule is enforced in code, not just the prompt.** The agent may
only cite a URL that its `web_search` tool actually returned — every other
citation is dropped, so hallucinated links can't leak in. A concrete status must
carry at least one valid citation; otherwise it is forced to `unknown`. So every
non-`unknown` label traces to a real, retrieved source.

**Multiple cited sources per project.** The agent returns a `citations` list of
`{source_url, finding}` pairs — each source paired with the specific finding it
supports — so a reader (and the dashboard) can see *where* each fact came from,
not just a single link.

The agent's status signals and confidence tiers follow the rubric documented
above, so every project is judged on one definition.

**Search budget.** `AGENT_MAX_SEARCHES` (env, default 5) caps how many web
searches the agent may run per project. It maps to LangGraph's `recursion_limit`
(`max_searches * 2 + 3`) so a runaway loop can't burn credits.

**Two LLM backends, switchable in `.env`** via `LLM_BACKEND`:

- `anthropic` (default) — personal Anthropic API key. Zero-setup for anyone cloning the repo, and the backend to use in CI (a static secret).
- `bedrock` — Claude through Amazon Bedrock using local AWS credentials (incl. SSO: `aws sso login --profile <name>`). Profile, region, and model id all come from `.env`; no account identifiers are committed.

Output: [`data/predictions.csv`](data/predictions.csv) —
`project_name, council, status, confidence, justification, citations, model, backend`
(`citations` is a JSON list).

Run it:

```bash
python -m pipeline.run                 # all projects
python -m pipeline.run --limit 3       # cheap smoke test
python -m pipeline.run --only "Salford Rise (Innovation Zone)"
```

Classification runs at `temperature=0` for reproducibility. Because the agent
chooses its own search path, runs are less perfectly deterministic than a fixed
prompt — the trade-off for much better recall on hard, sparsely-reported projects.

### Modelling approach: zero-shot, not trained

There is **no model training here.** We use a pre-trained model (Claude) for
**zero-shot classification** — it labels from the prompt's rules alone, having
seen zero labelled examples. The engineering value is the **retrieval + agent
design** loop, not weight training.

---

## Step 3 — present (done)

The results are shown as a **static, single-page map dashboard** in
[`docs/`](docs/) — plain HTML/CSS/JS with [MapLibre GL JS](https://maplibre.org/)
(loaded from a CDN). It has **no backend and no build step**: the page just
fetches one pre-computed `docs/data.json` and draws it, so it can be hosted
anywhere that serves static files (see *Hosting* below).

Each project is a circle on the map, **colour-coded by delivery status** and
sized by award value. Clicking a pin opens a panel with, in order:

1. **The plan** — a neutral one-line summary of what the project was funded to build.
2. **Status + confidence + round** badges.
3. **Why this status** — the agent's justification.
4. **Evidence** — every `{source, finding}` citation, each link opening the real source.
5. **Award record** — the GOV.UK bidder list the project came from.

The legend doubles as a filter (click a status to show/hide it), and a header
stat block summarises project count, total awarded, and how many are classified.

**Three small helper stages feed the dashboard** (each caches to a CSV so it is
only paid for once, not on every weekly refresh):

| Module | Job | Cache |
|--------|-----|-------|
| [`pipeline/geocode.py`](pipeline/geocode.py) | Look up a lat/lon for each project's town (Nominatim / OpenStreetMap). | [`data/locations.csv`](data/locations.csv) |
| [`pipeline/describe.py`](pipeline/describe.py) | Write the grounded "what it was funded to build" summary (one Tavily search + one Claude call per project, facts only). | [`data/descriptions.csv`](data/descriptions.csv) |
| [`pipeline/build_dashboard.py`](pipeline/build_dashboard.py) | Join base + predictions + locations + descriptions into `docs/data.json`. | — |

Geocoding and descriptions are **static** — a project's town and original
remit don't change week to week — so they are committed and reused; only
`pipeline.run` (the status) is re-run on a refresh.

```bash
python -m pipeline.geocode          # one-off: data/locations.csv
python -m pipeline.describe         # one-off: data/descriptions.csv
python -m pipeline.build_dashboard  # -> docs/data.json

# preview locally (fetch() needs http://, not file://)
cd docs && python3 -m http.server 8777   # then open http://localhost:8777
```

**Basemap note.** The map draws tiles from Esri's free World Topographic
service (light topographic style with terrain relief), which is cleared for
light public/embedded use with attribution. The basemap is a single-line source
swap in [`docs/app.js`](docs/app.js) — CARTO Voyager or OpenStreetMap standard
are drop-in alternatives; the underlying map data is OpenStreetMap either way.


---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# API keys for Step 2 (not needed for Step 1)
cp .env.example .env   # then fill in TAVILY_API_KEY and your chosen LLM backend
```

`.env` is git-ignored; keys are read from it and never hardcoded.

## Repository layout

```
data/        base dataset, predictions, locations & descriptions (CSV)
pipeline/    scraping, retrieval, the classification agent, geocoding,
             descriptions, retrospectives, and dashboard build
docs/        static MapLibre dashboard (index.html + styles.css + app.js + data.json)
```

## Keeping it up to date (productionisation)

The tracker is a **scheduled batch job**, not an always-on service, so it does
not need a running server. The intended "stays fresh automatically" path is a
**scheduled GitHub Actions workflow** (weekly cron) that re-runs `pipeline.run`
+ `pipeline.build_dashboard` and commits the refreshed `predictions.csv` and
`data.json` — free, versioned, and visible in the repo. Because CI can't use a
local AWS SSO session, the scheduled job should use `LLM_BACKEND=anthropic`
with an `ANTHROPIC_API_KEY` stored as a repository secret. The geocode and
describe stages are cached in-repo, so the weekly job only re-derives status.
An AWS equivalent (Lambda + EventBridge schedule, scaling to zero) is a natural
alternative if cloud deployment is required; a 24/7 EC2 instance would be
overkill for a job that runs occasionally.

## Hosting (public link)

The dashboard is fully static, so publishing it is just "serve the `docs/`
folder":

- **GitHub Pages** (free): push this repo to GitHub, then enable Pages and point
  it at `/docs`. You get a URL like `https://<user>.github.io/<repo>/`.
- **Netlify / Vercel / Cloudflare Pages** (free): drag-and-drop the `docs/`
  folder, or connect the repo and set the publish directory to `docs/`.

Only `docs/data.json` (plus the committed CSVs it is built from) ships —
no API keys are needed at page-load, because all LLM/search/geocoding work
happens ahead of time in the pipeline. Swap the basemap tiles (see the *Step 3*
note) before sending a high-traffic public link.
