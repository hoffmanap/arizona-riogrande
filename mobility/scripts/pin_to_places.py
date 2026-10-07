"""
Assign each device ping in the pin_report to the SafeGraph place it fell inside,
and compute unique visits (unique device-per-day) and visit density (unique
visits per square foot of building footprint) for every place in the study area.

Inputs:
  - PIN_REPORT: the pin_report file (.tsv or .tsv.gz). Expected columns (by
    name pattern, case-insensitive -- exact vendor header text can vary):
      Polygon ID, Hashed Device ID (or UDID), Lat of Visit, Lon of Visit,
      Unix Timestamp of Visit, Date, Time of Day, Day of Week, Time Zone
  - PLACES: el_paso_subarea_safegraph_places.geojson (individual SafeGraph
    place polygons for the study area, built earlier in this project; see
    mobility/data/). Must carry PLACEKEY, LOCATION_NAME, TOP_CATEGORY,
    WKT_AREA_SQ_METERS.

Output: mobility/outputs/place_visit_density.csv -- one row per place with
  unique_visits, days_covered, visits_per_day, building_sqft, and
  visits_per_sqft (the "visit density" metric).

Usage:
  python pin_to_places.py PIN_REPORT_PATH [--places PLACES_GEOJSON] [--out OUT_CSV]

Notes / assumptions (document these before trusting the output):
  - "Unique visit" = a distinct (device, place, calendar date) combination --
    i.e. a device that pings inside a place's polygon more than once in the
    same day counts as one visit, consistent with standard SafeGraph usage.
  - A ping is assigned to a place only when it falls INSIDE that place's
    footprint polygon (point-in-polygon). Pings that fall between buildings
    (sidewalk, parking lot, street) are not assigned to any place and are
    reported separately as "unmatched".
  - Building area used for density is each place's own SafeGraph polygon
    area (WKT_AREA_SQ_METERS -> converted to sqft), not the full parcel --
    this is the footprint SafeGraph itself measured for that business, which
    is what's available without a parcel-to-place crosswalk for every record.
  - If a device pings inside two overlapping places at once (shared-polygon
    retail, e.g. a strip mall parent + unit), the ping is counted for ALL
    places it falls inside -- a visit to a specific tenant still counts for
    the parent/shared polygon too, matching how SafeGraph's own shared
    polygons work.
"""
import argparse
import csv
import gzip
import sys
from pathlib import Path
from collections import defaultdict

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

SQM_TO_SQFT = 10.7639

COLUMN_PATTERNS = {
    'device': ['hashed device id', 'hashed ubermedia id', 'udid', 'device id'],
    'lat': ['lat of visit', 'latitude', 'lat'],
    'lon': ['lon of visit', 'lng of visit', 'longitude', 'lon', 'lng'],
    'date': ['date', 'visit date'],
    'timestamp': ['unix timestamp', 'timestamp'],
}


def find_col(columns, patterns):
    lower = {c.lower().strip(): c for c in columns}
    for pat in patterns:
        if pat in lower:
            return lower[pat]
    # fallback: substring match
    for pat in patterns:
        for lc, orig in lower.items():
            if pat in lc:
                return orig
    return None


def open_text(path):
    path = Path(path)
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8', errors='replace')
    return open(path, 'rt', encoding='utf-8', errors='replace')


def load_places(places_path):
    places = gpd.read_file(places_path)
    if places.crs is None:
        places = places.set_crs(4326)
    places = places.to_crs(4326)
    places['building_sqft'] = places['WKT_AREA_SQ_METERS'].astype(float) * SQM_TO_SQFT
    return places


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pin_report', help='Path to the pin_report .tsv or .tsv.gz file')
    ap.add_argument('--places', default=None,
                     help='Path to el_paso_subarea_safegraph_places.geojson (default: mobility/data/ next to this script)')
    ap.add_argument('--out', default=None, help='Output CSV path (default: mobility/outputs/place_visit_density.csv)')
    ap.add_argument('--chunk-size', type=int, default=250_000, help='Rows to buffer before a spatial join batch')
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    places_path = Path(args.places) if args.places else script_dir.parent / 'data' / 'el_paso_subarea_safegraph_places.geojson'
    out_path = Path(args.out) if args.out else script_dir.parent / 'outputs' / 'place_visit_density.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not places_path.exists():
        sys.exit(f"ERROR: places file not found at {places_path}. Pass --places explicitly.")

    print(f"Loading place polygons from {places_path} ...")
    places = load_places(places_path)
    print(f"  {len(places)} places loaded, CRS={places.crs}")

    pin_path = Path(args.pin_report)
    if not pin_path.exists():
        sys.exit(f"ERROR: pin report not found at {pin_path}")

    print(f"Scanning pin report {pin_path} ...")
    with open_text(pin_path) as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        col_device = find_col(header, COLUMN_PATTERNS['device'])
        col_lat = find_col(header, COLUMN_PATTERNS['lat'])
        col_lon = find_col(header, COLUMN_PATTERNS['lon'])
        col_date = find_col(header, COLUMN_PATTERNS['date'])
        missing = [n for n, c in [('device', col_device), ('lat', col_lat), ('lon', col_lon), ('date', col_date)] if c is None]
        if missing:
            sys.exit(f"ERROR: couldn't find columns for {missing} in header: {header}\n"
                      f"Edit COLUMN_PATTERNS at the top of this script to match your file's exact headers.")
        idx = {name: header.index(col) for name, col in
               [('device', col_device), ('lat', col_lat), ('lon', col_lon), ('date', col_date)]}
        print(f"  Using columns: device={col_device!r} lat={col_lat!r} lon={col_lon!r} date={col_date!r}")

        # unique_visits[placekey] = set of (device, date)
        unique_visits = defaultdict(set)
        unmatched_pings = 0
        total_pings = 0

        buf_rows = []

        def flush(buf_rows):
            nonlocal unmatched_pings
            if not buf_rows:
                return
            df = pd.DataFrame(buf_rows, columns=['device', 'date', 'lat', 'lon'])
            df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
            df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
            df = df.dropna(subset=['lat', 'lon'])
            if df.empty:
                return
            gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['lon'], df['lat']), crs=4326)
            joined = gpd.sjoin(gdf, places[['PLACEKEY', 'geometry']], how='left', predicate='within')
            matched = joined.dropna(subset=['PLACEKEY'])
            unmatched_pings += len(joined) - len(matched)
            for pk, dev, dt in zip(matched['PLACEKEY'], matched['device'], matched['date']):
                unique_visits[pk].add((dev, dt))

        for row in reader:
            total_pings += 1
            buf_rows.append((row[idx['device']], row[idx['date']], row[idx['lat']], row[idx['lon']]))
            if len(buf_rows) >= args.chunk_size:
                flush(buf_rows)
                buf_rows = []
                print(f"  ...{total_pings:,} pings scanned")
        flush(buf_rows)

    print(f"Total pings scanned: {total_pings:,}")
    print(f"Pings not inside any place polygon (sidewalk/parking/street): {unmatched_pings:,}")

    rows = []
    for _, place in places.iterrows():
        pk = place['PLACEKEY']
        visits = unique_visits.get(pk, set())
        n_visits = len(visits)
        days = {d for (_, d) in visits}
        n_days = len(days)
        sqft = place['building_sqft']
        rows.append({
            'PLACEKEY': pk,
            'LOCATION_NAME': place.get('LOCATION_NAME'),
            'TOP_CATEGORY': place.get('TOP_CATEGORY'),
            'STREET_ADDRESS': place.get('STREET_ADDRESS'),
            'unique_visits': n_visits,
            'days_with_any_visit': n_days,
            'visits_per_day': round(n_visits / n_days, 2) if n_days else 0,
            'building_sqft': round(sqft, 1) if pd.notna(sqft) else None,
            'visits_per_sqft': round(n_visits / sqft, 4) if sqft and sqft > 0 else None,
        })

    out_df = pd.DataFrame(rows).sort_values('unique_visits', ascending=False)
    out_df.to_csv(out_path, index=False)
    print(f"\nSaved {len(out_df)} places to {out_path}")
    print("\nTop 10 busiest places by unique visits:")
    print(out_df[['LOCATION_NAME', 'TOP_CATEGORY', 'unique_visits', 'visits_per_sqft']].head(10).to_string(index=False))


if __name__ == '__main__':
    main()
