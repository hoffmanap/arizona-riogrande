# Arizona-Rio Grande — Zoning Modernization

Parcel-level nonconformity analysis and policy-design tools for a 799-parcel subarea north of
Downtown El Paso (the Wyoming / Missouri / Yandell corridor and surrounding residential blocks),
built to support a Title 20 subarea rezoning.

**Live demo:** once hosted on GitHub Pages, the whole thing lives at one URL:

```
https://hoffmanap.github.io/arizona-riogrande/
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
| `landuse.html` | **New.** Standalone existing-land-use map: current use per parcel (single-family, duplex, multifamily/apartment, office, retail/commercial, restaurant/food service, automotive/industrial, health care, institutional/civic, vacant, mixed/other commercial, unclassified) as the primary color symbology, with detailed per-parcel tooltips (address, zoning district, established use detail, unit counts, lot/building area, coverage). Current-conditions map, not a policy proposal -- separate from the proposed subdistrict map in `story.html`. Fully self-contained, independently linkable. |

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
- **Zoning applies to genuinely contiguous, spatially-adjacent clusters of parcels, never to an
  independently-classified patchwork.** Subdistrict boundaries are built from true parcel-to-parcel
  physical adjacency (buffered polygon intersection at a 0.5 ft tolerance — not a street-name/
  address-number proxy, which produced non-contiguous block-face "islands" on diagonal blocks).
  Parcels are grouped into spatially-connected clusters; each cluster takes the majority subdistrict
  of its own parcels; any small isolated cluster is reassigned to match the subdistrict of its larger
  surrounding neighbor before the clusters are dissolved into the final district polygons. Every
  cluster is internally uniform — one subdistrict per cluster, no parcel-by-parcel exceptions within
  it — so a single block face can no longer straddle multiple incompatible typologies.
- **A north-to-south intensity gradient, not three independently-drawn subdistricts — and a corrected
  no-downzoning rule.** The prior version's "Subdistrict 2" (SF/duplex/small apartment/neighborhood
  office) was narrower than C-4, and the contiguity/region-growing step had pulled some C-4 parcels
  inside its polygon. Texas law does not allow a city to downzone a parcel's as-of-right uses, so any
  subdistrict whose footprint includes an existing C-4 parcel must permit at least everything C-4
  already permits there — that was a real error in the previous iteration, not a policy choice, and
  it is fixed in this version. The subarea still collapses to three subdistricts, but they are now
  organized as a gradient from residential in the north to highest-intensity (but still
  neighborhood-scale) commercial in the south, with boundaries set at real breaks in the per-street
  data rather than even thirds:
  - **Band A — Residential Core** (north of Montana: River, California, Nevada, Arizona, Rio Grande
    and their cross streets — zero C-4 parcels). Permits single-family, duplex, and each parcel's
    existing A-2/A-3/A-4 multifamily density; neighborhood office is limited to where A-O already
    allows it or as infill conversion.
  - **Band B — Transition Mixed-Use** (Montana itself, plus the Yandell and Wyoming C-4 corridors —
    Montana's own parcels are a mix of S-D, A-2, A-O and a little C-4). A strict superset of C-4
    (retail, personal service, office, restaurants, mixed-use residential up to 145 units/acre, 60 ft
    height, 1.5 FAR — every use a C-4 parcel here already holds stays as of right) plus broader
    multifamily than Band A, absorbing the Montana apartment/office cluster and the inner two
    commercial corridors into one coherent mixed-use tier.
  - **Band C — Higher-Intensity Neighborhood Corridor** (Missouri — confirmed centroid-latitude
    southernmost of the three corridors, closest to I-10, entirely C-4). Also a strict superset of
    C-4, carrying the subarea's broadest commercial intensity, but capped at C-4's own existing 60 ft
    / 1.5 FAR ceiling rather than an upzone, with a bulk step-down toward Band B so it reads as
    neighborhood-serving rather than a regional commercial strip.

  A separate "Unchanged Corridor" track still exists for commercial land that independently clears
  compliance on every parcel and so isn't rezoned at all. Boundaries are built from true
  parcel-to-parcel physical adjacency the same way as before (buffered polygon intersection, region
  growing, small-island reassignment to the surrounding majority) so the three bands are large,
  contiguous, legible polygons rather than a literal three-stripe ruler cut across the subarea.
- **No off-street parking requirement** anywhere in the subarea.
- **Setbacks are grandfathered**: every existing building's own as-built setback is its permanent
  legal minimum. New construction or redevelopment must match whichever of a lot's immediate
  physical neighbors has the shallower setback — a simple, fast, two-number comparison rather than
  a block-wide statistic.
- **Minimum lot size** is set per subdistrict at the smallest existing legitimate lot (GIS
  slivers/data artifacts excluded), rounded down to the nearest 1,000 sq ft — so every existing lot
  clears it by construction.
- **Density violations** (3+ units on R-5 land capped at 2) are resolved by legalizing the as-built
  unit count as "small apartment" under whichever band the parcel falls in, rather than requiring
  removal.

## Known limitations

- Front/rear/side setback orientation is estimated geometrically from each parcel's address street
  (no street-centerline layer was available) and should be field-verified, especially on corner lots
  — flagged in both the workbook and the map.
- A handful of parcels on an otherwise-uniform Band A face don't themselves match the
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
