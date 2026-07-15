/*
 * Levelling Up Fund delivery map.
 *
 * Loads the pipeline's published data.json and renders it: one marker per
 * project, sized by money awarded and coloured by delivery status. Clicking a
 * marker slides in a panel with the project's status, the agent's justification,
 * and a list of source citations -- the "progressive discovery" flow.
 *
 * This file does no analysis. Everything it shows was produced offline by the
 * Python pipeline; here we only render it.
 */

const STATUS_COLORS = {
  on_track: "#1a9850",
  delayed: "#f4a63f",
  stalled: "#d73027",
  rescoped: "#7b6cd0",
  cancelled: "#7a7a7a",
  completed: "#2b6cb0",
  unknown: "#b8b8b8",
};

const STATUS_LABELS = {
  on_track: "On track",
  delayed: "Delayed",
  stalled: "Stalled",
  rescoped: "Rescoped",
  cancelled: "Cancelled",
  completed: "Completed",
  unknown: "Unknown",
};

const CONFIDENCE_LABELS = { high: "High confidence", med: "Medium confidence", low: "Low confidence" };

// Marker diameter scales with award size (sqrt keeps area roughly proportional).
function markerSize(amount) {
  if (!amount) return 16;
  const scaled = Math.sqrt(amount / 1_000_000); // ~£1m..£50m -> 1..7
  return Math.max(14, Math.min(40, 11 + scaled * 4));
}

function gbp(amount) {
  if (amount == null) return "—";
  return "£" + amount.toLocaleString("en-GB");
}

const state = { markers: [], activeStatuses: new Set(Object.keys(STATUS_COLORS)) };

const map = new maplibregl.Map({
  container: "map",
  // OpenStreetMap standard raster tiles -- no API key required. Note: OSM's own
  // tile server is intended for light/dev use; swap to CARTO Voyager or Esri
  // before heavy public traffic (see README hosting notes).
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  },
  center: [-2.7, 53.9], // North West England
  zoom: 7.4,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

fetch("data.json")
  .then((r) => {
    if (!r.ok) throw new Error(`data.json ${r.status}`);
    return r.json();
  })
  .then((data) => {
    renderStats(data.summary);
    renderLegend();
    renderFooter(data.generated);
    map.on("load", () => addMarkers(data.projects));
    if (map.loaded()) addMarkers(data.projects);
  })
  .catch((err) => {
    document.getElementById("stats").innerHTML =
      `<span class="no-evidence">Could not load data.json (${err.message}). ` +
      `Run: python -m pipeline.build_dashboard</span>`;
  });

function renderStats(summary) {
  const el = document.getElementById("stats");
  el.innerHTML = `
    <div class="stat">
      <div class="stat-value">${summary.project_count}</div>
      <div class="stat-label">Projects</div>
    </div>
    <div class="stat">
      <div class="stat-value">£${(summary.total_awarded_gbp / 1e6).toFixed(0)}m</div>
      <div class="stat-label">Awarded</div>
    </div>
    <div class="stat">
      <div class="stat-value">${summary.classified_count}</div>
      <div class="stat-label">Classified</div>
    </div>`;
}

function renderLegend() {
  const el = document.getElementById("legend");
  const rows = Object.keys(STATUS_COLORS)
    .map(
      (s) => `
      <div class="legend-row" data-status="${s}">
        <span class="legend-swatch" style="background:${STATUS_COLORS[s]}"></span>
        <span>${STATUS_LABELS[s]}</span>
      </div>`
    )
    .join("");
  el.innerHTML = `<h2>Delivery status</h2>${rows}`;

  // Click a legend row to toggle that status on/off on the map.
  el.querySelectorAll(".legend-row").forEach((row) => {
    row.addEventListener("click", () => {
      const s = row.dataset.status;
      if (state.activeStatuses.has(s)) state.activeStatuses.delete(s);
      else state.activeStatuses.add(s);
      row.classList.toggle("muted", !state.activeStatuses.has(s));
      applyFilter();
    });
  });
}

function renderFooter(generated) {
  if (generated) {
    document.getElementById("footer-generated").textContent = `Updated ${generated}. `;
  }
}

function addMarkers(projects) {
  projects.forEach((p) => {
    const size = markerSize(p.amount_gbp);
    const elMarker = document.createElement("div");
    elMarker.className = "marker" + (p.area_wide ? " area-wide" : "");
    elMarker.style.width = `${size}px`;
    elMarker.style.height = `${size}px`;
    elMarker.style.background = STATUS_COLORS[p.status] || STATUS_COLORS.unknown;

    const marker = new maplibregl.Marker({ element: elMarker })
      .setLngLat([p.lon, p.lat])
      .addTo(map);

    elMarker.addEventListener("click", (e) => {
      e.stopPropagation();
      openPanel(p);
      map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 9), speed: 0.8 });
    });

    state.markers.push({ project: p, marker });
  });
}

function applyFilter() {
  state.markers.forEach(({ project, marker }) => {
    const visible = state.activeStatuses.has(project.status);
    marker.getElement().style.display = visible ? "" : "none";
  });
}

// ---- Slide-in detail panel -------------------------------------------------
const panel = document.getElementById("panel");
const panelBody = document.getElementById("panel-body");
document.getElementById("panel-close").addEventListener("click", closePanel);
map.on("click", closePanel); // click empty map to dismiss

function openPanel(p) {
  const statusColor = STATUS_COLORS[p.status] || STATUS_COLORS.unknown;
  const confidence = p.confidence ? CONFIDENCE_LABELS[p.confidence] || p.confidence : null;

  const citations = (p.citations || [])
    .map(
      (c) => `
      <div class="citation">
        <p class="finding">${escapeHtml(c.finding || "")}</p>
        ${
          c.source_url
            ? `<a href="${encodeURI(c.source_url)}" target="_blank" rel="noopener">${escapeHtml(
                c.source_url
              )}</a>`
            : ""
        }
      </div>`
    )
    .join("");

  panelBody.innerHTML = `
    <h2>${escapeHtml(p.project_name)}</h2>
    <p class="council">${escapeHtml(p.council)}${p.place ? " · " + escapeHtml(p.place) : ""}${
    p.area_wide ? " · area-wide programme" : ""
  }</p>

    ${
      p.summary
        ? `<div class="section-title">The plan</div>
           <p class="justification">${escapeHtml(p.summary)}</p>`
        : ""
    }

    <div class="badges">
      <span class="badge" style="background:${statusColor}">${STATUS_LABELS[p.status]}</span>
      ${confidence ? `<span class="badge soft">${confidence}</span>` : ""}
      ${p.round ? `<span class="badge soft">Round ${p.round}</span>` : ""}
    </div>

    <div class="amount">${gbp(p.amount_gbp)}</div>
    <div class="amount-label">Levelling Up Fund award</div>

    ${
      p.justification
        ? `<div class="section-title">Why this status</div>
           <p class="justification">${escapeHtml(p.justification)}</p>`
        : ""
    }

    <div class="section-title">Evidence${
      p.citations && p.citations.length ? ` (${p.citations.length})` : ""
    }</div>
    ${
      citations ||
      `<p class="no-evidence">No sources cited — status shown as unknown rather than guessed.</p>`
    }

    ${
      p.award_url
        ? `<div class="section-title">Award record</div>
           <a class="award-link" href="${encodeURI(
             p.award_url
           )}" target="_blank" rel="noopener">GOV.UK successful bidders list →</a>`
        : ""
    }`;

  panel.classList.add("open");
  panel.setAttribute("aria-hidden", "false");
}

function closePanel() {
  panel.classList.remove("open");
  panel.setAttribute("aria-hidden", "true");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
