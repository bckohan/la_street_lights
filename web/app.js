/* LA street-light assessment parcel map (MapLibre GL + PMTiles).
 *
 * Renders the parcels.pmtiles vector tiles as a choropleth of annual
 * assessment value, with no-value parcels in gray and the City of LA
 * boundary outlined. Color breaks/colors come from data/scale.json so the
 * page does no statistics itself. */

const BASE = location.origin + location.pathname.replace(/[^/]*$/, "");
const PARCELS_URL = "pmtiles://" + BASE + "data/parcels.pmtiles";
const DISTRICT_URL = "pmtiles://" + BASE + "data/district.pmtiles";
const STREETLIGHTS_URL = "pmtiles://" + BASE + "data/streetlights.pmtiles";
const DISTRICT_COLOR = "#2c7fb8";  // single color for the District 5500 footprint
const SOURCE_LAYER = "parcels";          // tippecanoe layer name
const BOUNDARY_COLOR = "#d11";
const USE_BASEMAP = !location.search.includes("nobasemap");
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

// Plain light style used when the basemap is disabled or fails to load.
const BLANK_STYLE = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#eee" } }],
};

// Register the pmtiles:// protocol.
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const HAS_HASH = location.hash.length > 1;  // e.g. #13/34.05/-118.25
const map = new maplibregl.Map({
  container: "map",
  style: USE_BASEMAP ? BASEMAP_STYLE : BLANK_STYLE,
  center: [-118.41, 34.02],
  zoom: 9,
  hash: true,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

// If the remote basemap style fails (offline), fall back to the blank style.
let recovered = false;
map.on("error", (e) => {
  if (USE_BASEMAP && !recovered && e && e.error && /style|fetch|load/i.test(String(e.error.message))) {
    recovered = true;
    map.setStyle(BLANK_STYLE);
  }
});

map.on("load", init);

async function init() {
  const scale = await (await fetch("data/scale.json")).json();
  try { LU_STATS = await (await fetch("data/landuse_stats.json")).json(); }
  catch (e) { console.warn("landuse stats not loaded:", e); }

  // Build a MapLibre `step` expression: color by assessment using the breaks.
  const fillColor = ["step", ["get", scale.field], scale.colors[0]];
  scale.breaks.forEach((b, i) => fillColor.push(b, scale.colors[i + 1]));

  map.addSource("parcels", { type: "vector", url: PARCELS_URL });

  // District 5500 footprint in one flat color, underneath everything. Its
  // coalesced regions survive low zoom (where individual parcels drop out),
  // so zoomed out you see the district extent; the choropleth draws on top
  // when zoomed in and only this base shows through any dropped-parcel gaps.
  map.addSource("district", { type: "vector", url: DISTRICT_URL });
  map.addLayer({
    id: "district-fill", type: "fill", source: "district", "source-layer": "district",
    paint: { "fill-color": DISTRICT_COLOR, "fill-opacity": 0.55 },
  });

  // No-value parcels (in city, not in the roll) — neutral gray. Only when
  // zoomed in; at low zoom the flat district color stands alone.
  map.addLayer({
    id: "parcels-novalue", type: "fill", source: "parcels", "source-layer": SOURCE_LAYER,
    minzoom: 12,
    filter: ["!", ["has", "assessment"]],
    paint: { "fill-color": scale.novalue_color, "fill-opacity": 0.55 },
  });

  // Parcels with an assessment — the value choropleth. Zoomed in only, so it
  // overlays (and takes over from) the flat district color.
  map.addLayer({
    id: "parcels-value", type: "fill", source: "parcels", "source-layer": SOURCE_LAYER,
    minzoom: 12,
    filter: ["has", "assessment"],
    paint: { "fill-color": fillColor, "fill-opacity": 0.8 },
  });

  // Thin parcel outlines, only when zoomed in.
  map.addLayer({
    id: "parcels-outline", type: "line", source: "parcels", "source-layer": SOURCE_LAYER,
    minzoom: 14,
    paint: { "line-color": "rgba(0,0,0,0.35)", "line-width": 0.4 },
  });

  // BSL streetlights — amber dots, drawn above parcels, toggleable.
  map.addSource("streetlights", { type: "vector", url: STREETLIGHTS_URL });
  map.addLayer({
    id: "streetlights", type: "circle", source: "streetlights", "source-layer": "streetlights",
    paint: {
      "circle-color": "#ff9e1b",
      "circle-stroke-color": "rgba(60,30,0,0.6)",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 12, 0, 15, 0.6],
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 12, 1.6, 16, 4.5],
      "circle-opacity": 0.9,
    },
  });

  // City boundary on top.
  try {
    const boundary = await (await fetch("data/city_boundary.geojson")).json();
    map.addSource("city-boundary", { type: "geojson", data: boundary });
    map.addLayer({
      id: "city-boundary", type: "line", source: "city-boundary",
      paint: { "line-color": BOUNDARY_COLOR, "line-width": 2.5 },
    });
    if (!HAS_HASH) {
      const b = bbox(boundary);
      map.fitBounds(b, { padding: 30, duration: 0 });
    }
  } catch (err) {
    console.warn("boundary not loaded:", err);
  }

  buildLegend(scale);
  wirePopup();
  wireSearch();
}

function buildLegend(scale) {
  const fmt = (n) => "$" + Math.round(n).toLocaleString();
  const el = document.getElementById("legend");
  const rows = [];
  const n = scale.breaks.length;
  for (let i = 0; i <= n; i++) {
    let label;
    if (i === 0) label = "< " + fmt(scale.breaks[0]);
    else if (i === n) label = "≥ " + fmt(scale.breaks[n - 1]);
    else label = fmt(scale.breaks[i - 1]) + " – " + fmt(scale.breaks[i]);
    rows.push(swatchRow(scale.colors[i], label));
  }
  rows.push(swatchRow(scale.novalue_color, "No assessment (in city)"));
  rows.push(swatchRow(DISTRICT_COLOR, "District 5500 (zoomed out)"));
  rows.push(
    `<div class="legend-row"><span class="swatch-line"></span><span>City boundary</span></div>`
  );
  rows.push(
    `<div class="legend-row" style="margin-top:6px">` +
    `<input type="checkbox" id="sl-toggle" checked style="margin:0 6px 0 0" />` +
    `<span class="swatch" style="background:#ff9e1b;width:12px;height:12px;border-radius:50%"></span>` +
    `<label for="sl-toggle">BSL streetlights</label></div>`
  );
  // Land-use filter checkboxes (toggle assessed parcels on/off by category).
  const luBoxes = LAND_USE_TYPES.map((t, i) =>
    `<label class="lu-item"><input type="checkbox" class="lu-cb" value="${t}" checked />${t}</label>`
  ).join("");
  rows.push(
    `<div style="margin-top:8px;font-weight:600">Land use ` +
    `<a id="lu-all" style="cursor:pointer;color:#2c7fb8;font-weight:400">all</a> · ` +
    `<a id="lu-none" style="cursor:pointer;color:#2c7fb8;font-weight:400">none</a></div>` +
    `<div class="lu-filter">${luBoxes}</div>` +
    `<div id="lu-readout"></div>`
  );

  el.innerHTML =
    `<div style="font-weight:600;margin-bottom:4px">Annual assessment</div>` + rows.join("");

  document.getElementById("sl-toggle").addEventListener("change", (e) => {
    map.setLayoutProperty("streetlights", "visibility", e.target.checked ? "visible" : "none");
  });

  const cbs = [...el.querySelectorAll(".lu-cb")];
  cbs.forEach((cb) => cb.addEventListener("change", () => applyLandUseFilter(cbs)));
  el.querySelector("#lu-all").addEventListener("click", () => {
    cbs.forEach((c) => (c.checked = true)); applyLandUseFilter(cbs);
  });
  el.querySelector("#lu-none").addEventListener("click", () => {
    cbs.forEach((c) => (c.checked = false)); applyLandUseFilter(cbs);
  });
  updateLuReadout(cbs);
}

// Update the legend readout: selected vs total votes and assessed value (%).
function updateLuReadout(cbs) {
  const ro = document.getElementById("lu-readout");
  if (!ro || !LU_STATS) return;
  const money = (n) =>
    n >= 1e6 ? "$" + (n / 1e6).toFixed(1) + "M" : "$" + Math.round(n).toLocaleString();
  let v = 0, a = 0;
  cbs.filter((c) => c.checked).forEach((c) => {
    const s = LU_STATS.categories[c.value];
    if (s) { v += s.votes; a += s.assessed; }
  });
  const T = LU_STATS.total;
  const pct = (x, t) => (t ? (x / t * 100) : 0).toFixed(1) + "%";
  ro.innerHTML =
    `<div style="margin-top:6px;border-top:1px solid #ddd;padding-top:5px;font-size:12px">` +
    `<b>Selected</b><br/>` +
    `Votes: ${v.toLocaleString()} / ${T.votes.toLocaleString()} (${pct(v, T.votes)})<br/>` +
    `Assessed: ${money(a)} / ${money(T.assessed)} (${pct(a, T.assessed)})</div>`;
}

// The 8 primary land-use categories (see the assessment roll / Engineer's Report).
const LAND_USE_TYPES = [
  "Residential", "Retail", "Office", "Industrial",
  "Institutional", "Public", "Utility", "Undeveloped",
];

// Show only the checked land-use categories — in both the zoomed-in value
// choropleth and the low-zoom uniform district fill.
function applyLandUseFilter(cbs) {
  const selected = cbs.filter((c) => c.checked).map((c) => c.value);
  const all = selected.length === cbs.length;
  const inSelected = ["in", ["get", "land_use"], ["literal", selected]];
  map.setFilter("parcels-value",
    all ? ["has", "assessment"] : ["all", ["has", "assessment"], inSelected]);
  map.setFilter("district-fill", all ? null : inSelected);
  updateLuReadout(cbs);
}

function swatchRow(color, label) {
  return `<div class="legend-row"><span class="swatch" style="background:${color}"></span><span>${label}</span></div>`;
}

// APN -> address, populated from the address index (used by the popup so the
// address need not be duplicated into every parcel tile).
const ADDR_BY_AIN = new Map();

// Per-land-use vote/assessed totals (from data/landuse_stats.json); the legend
// readout sums the selected categories against these.
let LU_STATS = null;

// Toggle the lighting-class explainer inside a popup (called from inline ⓘ).
function toggleLightingInfo(el) {
  const box = el.closest(".maplibregl-popup-content").querySelector(".lighting-info");
  if (box) box.style.display = box.style.display === "none" ? "block" : "none";
}

// Lighting-class summary (from the BSL Engineer's Report, §4.2.1).
const LIGHTING_INFO =
  `<div class="lighting-info" style="display:none;margin-top:6px;padding-top:6px;` +
  `border-top:1px solid #ddd;font-size:12px;color:#333">` +
  `<b>Lighting class</b> = the lighting type(s) serving the parcel:<br/>` +
  `<b>1 – Ornamental</b>: decorative neighborhood lighting; adds an aesthetic benefit.<br/>` +
  `<b>2 – Standard</b>: typical street lighting (security, safety, access).<br/>` +
  `<b>3 – Pedestrian</b>: smaller lights on existing poles; an incremental benefit.<br/>` +
  `Multiple (e.g. 1,2,3): the parcel benefits from more than one type; benefit points are combined.</div>`;

// HTML for a parcel's assessment popup (shared by click + address search).
function parcelPopupHTML(p) {
  const ain = String(p.ain || "");
  const dashed = ain.length === 10 ? `${ain.slice(0, 4)}-${ain.slice(4, 7)}-${ain.slice(7)}` : ain;
  const titleCase = (s) =>
    String(s).toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

  const address = ADDR_BY_AIN.get(ain);  // looked up from the address index
  let html = `<b>APN ${dashed}</b>`;
  if (address) html += `<br/>${titleCase(address)}`;

  if (p.assessment == null) {
    html += `<br/><i>Not in District 5500 (no assessment)</i>`;
    return html;
  }

  const money = "$" + Number(p.assessment).toLocaleString(undefined, { minimumFractionDigits: 2 });
  const hasClass = p.lighting_class != null && p.lighting_class !== "";
  const rows = [
    ["Assessment", money],
    ["Units", p.units],
    ["Land use", [p.land_use, p.parcel_size_land_use].filter(Boolean).join(" / ")],
    ["Lighting class", hasClass
      ? `${p.lighting_class} <a onclick="toggleLightingInfo(this)" ` +
        `title="What do lighting classes mean?" ` +
        `style="cursor:pointer;color:#2c7fb8;text-decoration:none">&#9432;</a>`
      : ""],
    ["Lot size (ac)", p.lot_size],
    ["Land-use benefit pts", p.land_use_benefit_points],
    ["Parcel-size benefit pts", p.parcel_size_benefit_points],
    ["Special benefit pts", p.special_benefit_points],
  ];
  html += `<hr style="margin:5px 0;border:none;border-top:1px solid #ddd"/>`;
  html += rows
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}: ${v}`)
    .join("<br/>");
  if (hasClass) html += LIGHTING_INFO;
  return html;
}

function wirePopup() {
  map.on("click", (e) => {
    // Streetlights sit on top — check them first.
    const lights = map.queryRenderedFeatures(e.point, { layers: ["streetlights"] });
    if (lights.length) {
      const l = lights[0].properties;
      const html =
        `<b>Streetlight ${l.SLID ?? ""}</b><br/>` +
        `Status: ${l.STATUS || "—"}<br/>` +
        `Pole: ${l.POSTDESC || "—"}<br/>` +
        `Lamp: ${l.LAMPA || "—"}`;
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
      return;
    }
    const feats = map.queryRenderedFeatures(e.point, {
      layers: ["parcels-value", "parcels-novalue"],
    });
    if (!feats.length) return;
    new maplibregl.Popup({ maxWidth: "300px" }).setLngLat(e.lngLat).setHTML(parcelPopupHTML(feats[0].properties)).addTo(map);
  });
  map.on("mouseenter", "parcels-value", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "parcels-value", () => (map.getCanvas().style.cursor = ""));
}

// Self-contained address search over the local parcel address index
// (web/data/addresses.tsv.gz) — no external geocoding service.
function wireSearch() {
  const input = document.getElementById("search-input");
  const list = document.getElementById("search-results");
  let marker = null, timer = null, active = -1;
  let results = [];                              // A-indices of current matches
  let A = { addr: [], ain: [], lng: [], lat: [], n: 0 };  // parsed index

  const titleCase = (s) => String(s).toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  const clear = () => { list.innerHTML = ""; results = []; active = -1; };

  (async function loadIndex() {
    input.disabled = true; input.placeholder = "Loading addresses…";
    try {
      // Gzipped index, decompressed in the browser. We check the gzip magic
      // bytes so it works whether the host serves the .gz raw (most static
      // hosts) or already-decompressed (Content-Encoding: gzip).
      const buf = new Uint8Array(await (await fetch("data/addresses.tsv.gz")).arrayBuffer());
      let text;
      if (buf[0] === 0x1f && buf[1] === 0x8b) {
        const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
        text = await new Response(stream).text();
      } else {
        text = new TextDecoder().decode(buf);
      }
      const addr = [], ain = [], lng = [], lat = [];
      for (const line of text.split("\n")) {
        if (!line) continue;
        const t1 = line.indexOf("\t");
        const t2 = line.indexOf("\t", t1 + 1);
        const t3 = line.indexOf("\t", t2 + 1);
        const a = line.slice(0, t1), id = line.slice(t1 + 1, t2);
        addr.push(a);
        ain.push(id);
        lng.push(+line.slice(t2 + 1, t3));
        lat.push(+line.slice(t3 + 1));
        ADDR_BY_AIN.set(id, a);
      }
      A = { addr, ain, lng, lat, n: addr.length };
      input.placeholder = `Search ${A.n.toLocaleString()} addresses…`;
    } catch (e) {
      input.placeholder = "Address index unavailable";
      console.warn("address index load failed:", e);
    } finally {
      input.disabled = false;
    }
  })();

  function search(q) {
    const Q = q.toUpperCase();
    const hits = [];
    for (let i = 0; i < A.n; i++) {
      const idx = A.addr[i].indexOf(Q);
      if (idx >= 0) { hits.push({ i, idx }); if (hits.length >= 50) break; }
    }
    hits.sort((a, b) => a.idx - b.idx || (A.addr[a.i] < A.addr[b.i] ? -1 : 1));
    results = hits.slice(0, 8).map((h) => h.i);
    render();
  }

  function render() {
    if (!results.length) { list.innerHTML = "<li style='color:#888'>No matches</li>"; return; }
    list.innerHTML = results.map((i, k) => `<li data-k="${k}">${titleCase(A.addr[i])}</li>`).join("");
    [...list.querySelectorAll("li[data-k]")].forEach((li) => {
      li.onclick = () => choose(results[+li.dataset.k]);
    });
  }

  function choose(i) {
    const lng = A.lng[i], lat = A.lat[i];
    if (marker) marker.remove();
    marker = new maplibregl.Marker({ color: "#d11" }).setLngLat([lng, lat]).addTo(map);
    clear();
    input.value = titleCase(A.addr[i]);
    map.flyTo({ center: [lng, lat], zoom: 17, speed: 2.4 }); // 2x the default flight speed (1.2)
    // Snap to the parcel under the address and open its popup. Try right away
    // (tiles may already be present) and again on each "idle" (as tile batches
    // finish loading after the fly-in), detaching once a parcel is found.
    const onIdle = () => { if (snapToParcel([lng, lat])) map.off("idle", onIdle); };
    map.on("idle", onIdle);
    onIdle();
    setTimeout(() => map.off("idle", onIdle), 12000);
  }

  // Returns true (and opens a popup) once a parcel is found under/near lngLat.
  function snapToParcel(lngLat) {
    const pt = map.project(lngLat);
    const layers = ["parcels-value", "parcels-novalue"];
    let feats = map.queryRenderedFeatures(pt, { layers });
    if (!feats.length) {
      // Address fell on a street/gap — search a box and take the nearest parcel.
      const box = [[pt.x - 40, pt.y - 40], [pt.x + 40, pt.y + 40]];
      const near = map.queryRenderedFeatures(box, { layers });
      if (!near.length) return false;
      near.sort((a, b) => featDist(a, lngLat) - featDist(b, lngLat));
      feats = near;
    }
    new maplibregl.Popup({ maxWidth: "300px" }).setLngLat(lngLat).setHTML(parcelPopupHTML(feats[0].properties)).addTo(map);
    return true;
  }

  // Rough distance (deg) from a feature's first vertex to a point — good enough
  // to pick the closest of a few candidate parcels.
  function featDist(f, [lng, lat]) {
    let c = f.geometry && f.geometry.coordinates;
    while (Array.isArray(c) && Array.isArray(c[0])) c = c[0];
    if (!Array.isArray(c)) return Infinity;
    return (c[0] - lng) ** 2 + (c[1] - lat) ** 2;
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 3 || !A.n) { clear(); return; }
    timer = setTimeout(() => search(q), 150); // debounce keystrokes
  });

  input.addEventListener("keydown", (e) => {
    const items = [...list.querySelectorAll("li[data-k]")];
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); }
    else if (e.key === "Enter") { if (results.length) choose(results[active >= 0 ? active : 0]); return; }
    else return;
    items.forEach((li, i) => li.classList.toggle("active", i === active));
    e.preventDefault();
  });

  document.addEventListener("click", (e) => {
    if (!document.getElementById("search").contains(e.target)) clear();
  });
}

// Bounding box [[w,s],[e,n]] of a GeoJSON (Multi)Polygon FeatureCollection.
function bbox(geojson) {
  let w = 180, s = 90, e = -180, n = -90;
  const visit = (c) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]);
      s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(visit);
  };
  (geojson.features || []).forEach((f) => visit(f.geometry.coordinates));
  return [[w, s], [e, n]];
}
