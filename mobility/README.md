# Mobility data scripts

Cell-phone mobility analysis for the Five Points / Sunset Heights subarea,
grounding the zoning recommendations in actual visitor/traffic patterns.

## Data (`data/`)

- `el_paso_subarea_safegraph_places.geojson` -- individual SafeGraph place
  polygons within the study area (398 places), with building footprint area.
- `el_paso_subarea_parcel_compliance.geojson` -- parcel-level compliance data
  from the zoning analysis (same as the repo root's data, included here too
  for convenience).
- `study_area_street_centerlines.geojson` -- street centerlines for the study
  area, pulled from the City of El Paso's own GIS (`Streets/EPCenterline`),
  clipped to the subarea bounding box.

## Scripts (`scripts/`)

| Script | What it does | Answers |
|---|---|---|
| `local_regional_draw.py` | Resident/worker split + home/work distance-from-site bands, from the resident_worker_report and CEL/CDL distance summary. | Is visitation local or a regional draw? |
| `pin_to_places.py` | Assigns every device ping in the pin_report to the SafeGraph place it fell inside; computes unique visits (unique device-per-day) and visit density (unique visits / building sqft) per place. | Which places/areas get the most foot traffic? |
| `pathing_mode_street.py` | Reconstructs each device's travel path from the pathing_x_report, classifies each hop as pedestrian or vehicular by implied speed, and attributes it to the nearest study-area street (via a buffered centerline). | Which streets carry pedestrian vs. vehicular traffic? |

All three scripts take the raw report file as a command-line argument (or
default to reading from the current folder) and auto-locate the matching
data files relative to the script's own location, so they run the same way
on your machine as they do here -- no hardcoded paths.

```
python local_regional_draw.py "C:\path\to\your\reports"
python pin_to_places.py "C:\path\to\your\reports\pin_report.tsv.gz"
python pathing_mode_street.py "C:\path\to\your\reports\pathing_x_report.tsv.gz"
```

Run `python <script>.py --help` for every option (buffer width, speed
thresholds, output location, etc.) -- each script's module docstring also
documents its assumptions and known edge cases up front; read that before
trusting the numbers on a methodology this new.

**Note on column names:** these scripts were built from screenshots of the
pin_report and pathing_x_report column headers, not the actual files (which
were too large to upload) -- they weren't run against real pin/pathing data
before being committed, only against synthetic data matching the same
schema. Each script prints which columns it matched when you run it ("Using
columns: ..."); if that doesn't look right, or the script errors out
complaining it can't find a column, open the script and edit the
`COLUMN_PATTERNS` dict near the top to match your file's exact headers, then
re-run.

## Outputs (`outputs/`)

Generated CSVs land here by default: `place_visit_density.csv`,
`street_mode_split.csv`, `street_activity_by_dow.csv`. Not checked into the
repo (gitignored via `.gitkeep` placeholder) since they're derived from
report files that aren't in the repo either -- regenerate by re-running the
scripts against your local copies of the reports.
