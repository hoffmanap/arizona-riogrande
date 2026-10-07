# Mobility data scripts

Cell-phone mobility analysis for the Arizona-Rio Grande subarea,
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
| `dwell_by_category.py` | Joins the pin_report (device pings) to SafeGraph place polygons to find each device's dominant place category, then joins that onto the device's row in the dwell_time report by device ID. Outputs dwell-time-bucket shares per category. | Does dwell time differ by what kind of business someone visited? Needs both `pin_report` AND `dwell_time_report` as input -- neither file alone has both place and dwell-time info. |
| `street_traffic_origin.py` | Re-runs the pathing_x_report street/mode assignment (same logic as `pathing_mode_street.py`), then joins each device's home (CEL) distance from the resident_worker_report to classify its movement as local-origin or distant-origin. | Is a street's traffic mostly local residents or regional pass-through? Needs both `pathing_x_report` AND `resident_worker_report` as input. Output (`street_traffic_origin.csv`, now checked into `data/`) is wired into the "Trip origin" toggle on visits.html -- **important caveat**: only 2-16% of segments per street could be matched to a resolved home location at all (the rest are "unresolved", not "distant" -- the vendor just couldn't match that device to a home record). Among the resolved segments, 64-100% are local depending on the street, and almost none are from more than 3 miles away, so confirmed regional pass-through traffic looks rare -- but that finding only covers a small, uneven sample of total movement, not the full traffic volume. |
| `street_segment_classification.py` | Same pathing/resident-worker join as `street_traffic_origin.py`, but assigns every movement to the actual block-level street-centerline feature (there are already 175 of these across 21 streets -- the data is block-by-block, we just hadn't been using it that way) instead of collapsing to the whole named street, labels each block's two cross streets, and joins SafeGraph place visits directly by lat/lon (no more address-string matching). Classifies each block as LOCAL / PASS-THROUGH / DESTINATION by comparing its traffic volume and visits-per-traffic ratio to the subarea median -- **Lessors of Real Estate is excluded from the visit totals used for classification**, since that category mostly measures residents dwelling at home rather than commercial draw (see the dwell-by-category caveat in mobility.html), and including it would mislabel apartment-heavy blocks as commercial "destinations." | Which specific blocks (not whole streets) are pass-through, destination, or quiet/local? Needs `pathing_x_report` AND `resident_worker_report` as input (same files as `street_traffic_origin.py`) -- `study_area_street_centerlines.geojson`, the SafeGraph places geojson, and `place_visit_density.csv` are already in the repo and auto-detected. Output (`street_segment_classification.csv`) isn't wired into any page yet -- run this and send back the CSV to light up a per-segment toggle on the street traffic map. |

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

**Methodology correction (2026-10-07) -- mode is classified per TRIP, not per
hop:** `pathing_mode_street.py`, `street_traffic_origin.py`, and
`street_segment_classification.py` all reconstruct a device's path as
consecutive ping-to-ping hops, then need to call each hop pedestrian or
vehicular from its implied speed. Classifying each hop independently from its
own instantaneous speed meant a car briefly slowed by a stoplight or
congestion could get that one hop mislabeled "pedestrian," even though it's
the same car the whole way through. All three scripts now group a device's
pings into trips first (bounded by `--max-gap-min`, the same threshold
already used to avoid stitching together unrelated movements), classify each
trip's mode from its OVERALL speed (total trip distance / total trip time),
and apply that one label to every hop in the trip -- so a momentary slowdown
gets averaged into the trip's pace instead of flipping that hop's mode on its
own, while a genuine mode change (park the car, then walk) still splits
correctly, since that's exactly what a >`max-gap-min` pause already
represents as a new trip. If you ran any of these scripts before this date,
**the mode counts are worth re-running** -- re-running against the same raw
report files with the current script version will apply this fix.

## Outputs (`outputs/`)

Generated CSVs land here by default: `place_visit_density.csv`,
`street_mode_split.csv`, `street_activity_by_dow.csv`. Not checked into the
repo (gitignored via `.gitkeep` placeholder) since they're derived from
report files that aren't in the repo either -- regenerate by re-running the
scripts against your local copies of the reports.
