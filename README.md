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
| **2. Ground truth** | Hand-code delivery status from primary per-project sources (council reports, official openings, GOV.UK case studies, local press) as an independent validation sample. | ✅ Done |
| **3. Retrieve + classify** | A LangGraph ReAct agent researches each project — searching the web (Tavily) in a loop until it can justify a status with Claude — returning status, confidence, a justification, and multiple `{source, finding}` citations. | ✅ Done |
| **4. Validate** | Compare pipeline output to ground truth; report precision/recall per status, a confusion matrix, and confidence calibration — surfacing weak spots honestly. | ✅ Done |
| **5. Present** | Static, zero-backend map dashboard: every project pinned and colour-coded by status, with a click-through panel showing the original plan, the inferred status, and the cited evidence behind it. | ✅ Done |

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

## Step 2 — ground truth (done)

To validate the automated pipeline (Step 4) we need an independent, human-verified
label for each project's delivery status.

**Why not just use the NAO report / PAC follow-up?** They were the obvious
candidate, but on inspection both are **programme-level**: the National Audit
Office report and Public Accounts Committee follow-up assess LUF/Towns Fund
delivery *in aggregate* (spend profiles, national delay rates) and only touch a
handful of North West projects qualitatively (e.g. Knowsley as an interview
case study). They do **not** publish a per-project status we could code.

**Approach.** We hand-coded status for **11 of the 27 projects** from *primary
per-project sources*, prioritising, in order of preference:

1. **Council pages / committee reports** (e.g. `blackpool.gov.uk` Town Deal board papers)
2. **GOV.UK case studies** (e.g. the £20m Liverpool culture investment)
3. **Reputable regional press** (Place North West, LancsLive, Manchester Evening News)

Each label cites a specific URL and records the reasoning in a `notes` field —
including deliberately-flagged hard cases (e.g. *rescoped vs delayed* for Eden
Project Morecambe, *rescoped vs stalled* for Barrow town centre).

Output: [`data/ground_truth.csv`](data/ground_truth.csv) —
`project_name, status, confidence, source, notes` (joins to the base dataset on `project_name`).

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

Every label carries a `confidence` grade reflecting **evidence strength**, so a
weak judgement is never presented as a strong one:

- **`high`** — a dated, physical event from a primary source (e.g. "construction began Oct 2024"). *Salford Rise, Wirral Woodside, Radcliffe.*
- **`med`** — credible progress but partly the council's own framing. *Haigh Hall, Barrow.*
- **`low`** — only forward-looking language ("progress *expected* in 2025"). *Liverpool Docks — the softest label in the set.*

Step 3's classifier must reproduce this same output (status **+ confidence +**
a supporting quote), and Step 4 can then report accuracy **stratified by
confidence** — the low-confidence cases are exactly where human and model are
most likely (and most interestingly) to disagree.

Status distribution in the sample: `on_track` ×7, `rescoped` ×2, `delayed` ×1,
`completed` ×1. **No `stalled` or `cancelled` labels** — we found no primary
evidence of either among these North West awards, so they are honestly absent
rather than invented. This means Step 4 can measure precision/recall for the
four observed classes only.

---

## Limitations & honesty notes

- **North West only.** See the scope banner above.
- **Validation sample is 11/27 projects**, and skewed toward projects with
  strong public coverage — precision/recall in Step 4 should be read with that
  in mind.
- Ground-truth labels reflect the delivery position at time of research; a
  "completed" flagship element (e.g. Colne Market Hall) may sit inside a wider
  programme that is still ongoing — captured in the `notes` field.
- Later steps will add the strict rule that **every status classification must
  trace back to a real retrieved source** — no guessing.

---

## Step 3 — retrieve + classify (done)

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

The agent's status signals and confidence tiers are the same rubric documented
in Step 2 above, so ground truth and predictions are judged on one definition.

**Search budget.** `AGENT_MAX_SEARCHES` (env, default 5) caps how many web
searches the agent may run per project. It maps to LangGraph's `recursion_limit`
(`max_searches * 2 + 3`) so a runaway loop can't burn credits.

**Two LLM backends, switchable in `.env`** via `LLM_BACKEND`:

- `anthropic` (default) — personal Anthropic API key. Zero-setup for anyone cloning the repo, and the backend to use in CI (a static secret).
- `bedrock` — Claude through Amazon Bedrock using local AWS credentials (incl. SSO: `aws sso login --profile <name>`). Profile, region, and model id all come from `.env`; no account identifiers are committed.

Output: [`data/predictions.csv`](data/predictions.csv) —
`project_name, council, status, confidence, justification, citations, model, backend`
(`citations` is a JSON list; joins to `ground_truth.csv` on `project_name` for Step 4).

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
seen zero labelled examples. Consequently `ground_truth.csv` is **not** training
data: it is a held-out **validation / gold set** used only in Step 4 to *measure*
how often the zero-shot predictions match careful human judgement. The
engineering value is the **retrieval + agent design + evaluation** loop, not
weight training.

---

## Step 4 — validate (done)

[`pipeline/validate.py`](pipeline/validate.py) is the project's honesty check. It
joins `data/predictions.csv` to `data/ground_truth.csv` on `project_name`
(inner join = the labelled subset) and reports how well the automated pipeline
agrees with careful human judgement:

- **Overall accuracy** and **macro-averaged F1** on the labelled subset.
- **Precision / recall / F1 per status** (via scikit-learn) — so we can see, for
  example, whether `rescoped` is harder to call than `completed`.
- **Confusion matrix** — which statuses get mixed up (the interesting failure
  mode is `rescoped` vs `delayed`, the same boundary flagged in the ground truth).
- **Accuracy by predicted confidence** — a calibration check: are the agent's
  `high`-confidence calls actually more often right than its `low`-confidence ones?
- **Per-project agreement table** — disagreements listed first, so nothing is
  hidden.

The report is written to [`validation_report.md`](validation_report.md).

```bash
python -m pipeline.run        # produces data/predictions.csv (needs API keys)
python -m pipeline.validate   # scores it -> validation_report.md
```

> **Current result (11-project gold set):** 55% exact-status accuracy, macro-F1
> 0.54. Every disagreement is an *adjacent* status (e.g. `on_track` vs
> `delayed`) where the agent read **fresher** evidence than the hand-coded
> label — not a hallucination. The full breakdown lives in
> [`validation_report.md`](validation_report.md).

**Reading the numbers honestly:** the gold set is only 11 projects, so a single
miss swings a per-status metric substantially. The report says this inline —
treat the figures as *directional evidence that the pipeline broadly tracks
reality*, not a precise accuracy guarantee. Expanding the gold set is the most
valuable next improvement.


---

## Step 5 — present (done)

The results are shown as a **static, single-page map dashboard** in
[`dashboard/`](dashboard/) — plain HTML/CSS/JS with [MapLibre GL JS](https://maplibre.org/)
(loaded from a CDN). It has **no backend and no build step**: the page just
fetches one pre-computed `dashboard/data.json` and draws it, so it can be hosted
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
| [`pipeline/build_dashboard.py`](pipeline/build_dashboard.py) | Join base + predictions + locations + descriptions into `dashboard/data.json`. | — |

Geocoding and descriptions are **static** — a project's town and original
remit don't change week to week — so they are committed and reused; only
`pipeline.run` (the status) is re-run on a refresh.

```bash
python -m pipeline.geocode          # one-off: data/locations.csv
python -m pipeline.describe         # one-off: data/descriptions.csv
python -m pipeline.build_dashboard  # -> dashboard/data.json

# preview locally (fetch() needs http://, not file://)
cd dashboard && python3 -m http.server 8777   # then open http://localhost:8777
```

**Basemap note.** The map currently draws tiles from OpenStreetMap's own tile
server, which is fine for local/demo use. Before putting a high-traffic public
link out, swap the tile URL in [`dashboard/app.js`](dashboard/app.js) to a
provider cleared for embedding (e.g. CARTO Voyager — a one-line change); the map
data is still OpenStreetMap underneath.


---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# API keys for Step 3 (not needed for Steps 1–2)
cp .env.example .env   # then fill in TAVILY_API_KEY and your chosen LLM backend
```

`.env` is git-ignored; keys are read from it and never hardcoded.

## Repository layout

```
data/        base dataset, ground-truth, predictions, locations & descriptions (CSV)
pipeline/    scraping, retrieval, the classification agent, geocoding,
             descriptions, dashboard build, and validation
dashboard/   static MapLibre dashboard (index.html + styles.css + app.js + data.json)
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

The dashboard is fully static, so publishing it is just "serve the `dashboard/`
folder":

- **GitHub Pages** (free): push this repo to GitHub, then enable Pages. Pages
  serves from the repo root, `/docs`, or a branch — not an arbitrary subfolder —
  so either rename `dashboard/` → `docs/` and point Pages at `/docs`, or publish
  it via a `gh-pages` branch. You get a URL like `https://<user>.github.io/<repo>/`.
- **Netlify / Vercel / Cloudflare Pages** (free): drag-and-drop the `dashboard/`
  folder, or connect the repo and set the publish directory to `dashboard/`.

Only `dashboard/data.json` (plus the committed CSVs it is built from) ships —
no API keys are needed at page-load, because all LLM/search/geocoding work
happens ahead of time in the pipeline. Swap the basemap tiles (see the *Step 5*
note) before sending a high-traffic public link.
