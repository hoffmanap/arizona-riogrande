# Arizona-Rio Grande — Zoning Modernization

Parcel-level nonconformity analysis and policy-design tools for a 799-parcel subarea north of
Downtown El Paso (the Wyoming / Missouri / Yandell corridor and surrounding residential blocks),
built to support a Title 20 subarea rezoning.

**Live demo:** once hosted on GitHub Pages, the whole thing lives at one URL:

```
https://hoffmanap.github.io/arizona-riogrande/>/
```

That URL loads `index.html`, a two-tab landing page with both tools embedded — no separate links
to hand anyone.

## What's here

| File | What it is |
|---|---|
| `index.html` | **Start here.** Landing page with two tabs: "The Story & Proposed Subdistricts" and "Build Your Own District." Each tab loads the corresponding file below in an iframe. |
| `story.html` | Scrollytelling walkthrough of the subarea's nonconformity patterns, the Legal Nonconforming registry cross-check, and the final proposed two-subdistrict map. Fully self-contained (data embedded inline) — can also be opened directly, or linked to on its own. |
| `builder.html` | Interactive sandbox: pick a set of parcels (by preset, zoning district, or clicking the map), set your own lot-size/setback/parking/use standards with sliders and checkboxes, and watch compliance recalculate live against the real measured conditions on every parcel. Also fully self-contained and independently linkable. |
| `El_Paso_Subarea_Parcel_Compliance_Analysis.xlsx` | The full per-parcel audit trail: methodology, use/setback/parking/lot-area compliance detail, the Legal Nonconforming registry cross-check, and the R-5 unit-count review — this is the source of truth `story.html` and `builder.html` summarize and visualize. |
| `el_paso_subarea_parcel_compliance.geojson` | Parcel-level compliance data as plain GeoJSON, for use in QGIS/ArcGIS or any other GIS tool outside the browser tools here. |
| `el_paso_subarea_legal_nonconforming_points.geojson` | The City's own Legal Nonconforming registry points that fall in or near the subarea, for the same purpose. |

`story.html` and `builder.html` each carry their own data inline (no separate data files to keep
in sync) — open either one directly as a normal HTML file and it works with no server, no build
step, and no network access beyond loading Leaflet, Google Fonts, and the basemap tiles (Esri's
World Light Gray Canvas + Reference layers — grayscale with street labels, free, no API key) from
their respective CDNs.

## Hosting on GitHub Pages

1. Push this repo's contents to the root of a GitHub repository (or to a `/docs` folder, or a
   `gh-pages` branch — whichever convention you prefer).
2. In the repo's **Settings → Pages**, point GitHub Pages at that location.
3. GitHub serves `index.html` automatically at the repo's Pages URL — nothing else to configure.

No other setup is required. All three HTML files are static, dependency-free beyond the two CDN
includes (Leaflet, Google Fonts), and work the same locally (double-click to open in a browser) as
they do hosted.

## Methodology summary

Built from the City of El Paso's parcel and building-footprint layers, Regrid parcel/zoning
attributes (including address-point counts used to estimate multifamily unit counts), SafeGraph
business-location data (both a citywide extract and a ZIP-scoped 79902 extract, cross-checked
against each other), and El Paso Title 20 Appendices A–C, cross-referenced against the City's own
Legal Nonconforming registry (`Planning/PlanningZoning` MapServer).

Key design decisions, in brief (full detail in the workbook's Methodology tab and in `story.html`'s
footer):

- **Established use** is cross-verified across three signals — SafeGraph business matches, Regrid's
  structural class (assessor state-class code + LBCS description), and Regrid's mailing-address-point
  count — rather than trusting any one source alone. Where a building's address-point count
  substantially exceeds its on-record structural class with no commercial explanation, the address
  count is treated as more current evidence of actual occupancy than a potentially stale assessor
  record.
- **Zoning applies at the block-face level, never parcel by parcel.** Every parcel on a given block
  face (one side of one street, within one hundred-address block) carries the same subdistrict
  assignment, use permissions, and dimensional standards — there are no parcel-by-parcel carve-outs.
- **As few subdistricts as possible.** The subarea collapses to three new subdistricts — Subdistrict 1
  (single-family and duplex only, applied only where a face is already entirely that), Subdistrict 2
  (SF/duplex/small apartment/neighborhood office & personal service, applied to most other faces),
  and Subdistrict 3 (higher-intensity commercial, reserved for Yandell — the corridor street
  confirmed closest to the highway — permitting a broader commercial use range than Subdistrict 2
  while still carrying Yandell's existing houses and apartments as permitted uses) — plus a separate
  "Unchanged Corridor" track for commercial land that independently clears compliance on every
  parcel and so isn't rezoned at all.
- **No off-street parking requirement** anywhere in the subarea.
- **Setbacks are grandfathered**: every existing building's own as-built setback is its permanent
  legal minimum. New construction or redevelopment must match whichever of a lot's immediate
  physical neighbors has the shallower setback — a simple, fast, two-number comparison rather than
  a block-wide statistic.
- **Minimum lot size** is set per subdistrict at the smallest existing legitimate lot (GIS
  slivers/data artifacts excluded), rounded down to the nearest 1,000 sq ft — so every existing lot
  clears it by construction.
- **Density violations** (3+ units on R-5 land capped at 2) are resolved by legalizing the as-built
  unit count as "small apartment" under Subdistrict 2, rather than requiring removal.

## Known limitations

- Front/rear/side setback orientation is estimated geometrically from each parcel's address street
  (no street-centerline layer was available) and should be field-verified, especially on corner lots
  — flagged in both the workbook and the map.
- A handful of parcels on an otherwise-uniform Subdistrict 1 face don't themselves match the
  single-family/duplex pattern; these are listed as named individual exceptions rather than reasons
  to redraw the subdistrict boundary (see the "n_exceptions" count on the subdistrict map).
- 37 parcels show a zoning-source conflict between the assessor's own zoning field and Regrid's —
  these need manual resolution before being treated as final. See the workbook's Data Quality Flags
  tab.

## Updating the data

The underlying analysis (parcel joins, use classification, subdistrict assignment, compliance
checks) is a Python/GeoPandas pipeline, not something maintained by hand in these HTML files. If the
source data changes (new SafeGraph extract, corrected parcel data, a different policy assumption),
the pipeline needs to be re-run and `story.html`/`builder.html` regenerated from its output — these
two files are a snapshot, not a live query.
