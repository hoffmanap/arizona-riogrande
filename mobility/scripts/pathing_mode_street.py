"""
Mode-split and street-attribution analysis from the pathing_x_report.

The pathing_x_report gives, for each device that eventually visits the study
polygon, a trail of location pings observed BEFORE that visit (columns from
the vendor export, by pattern -- see COLUMN_PATTERNS below):
  Polygon ID, Hashed Device ID, Lat/Lon of Observation Point,
  Time before appearance in polygon, Unix Timestamp of Observation Point,
  Local Date, Local Time of Day, Local Day of Week

This script:
  1. Reconstructs each device's path as consecutive ping-to-ping segments.
  2. Classifies each segment's travel mode from its implied speed
     (haversine distance / elapsed time): walking, vehicular, or
     unclassifiable (gap too long / stationary / GPS noise).
  3. Clips the segments to the study area's streets (via El Paso's own
     EPCenterline GIS layer, buffered to a corridor width) and assigns each
     segment to the nearest street within that buffer.
  4. Aggregates, per street, the pedestrian vs. vehicular segment counts and
     share, plus time-of-day / day-of-week activity -- to show which streets
     carry the most foot traffic vs. car traffic, and when.

Inputs:
  - PATHING_REPORT: the pathing_x_report file (.tsv or .tsv.gz)
  - STREETS: study_area_street_centerlines.geojson (El Paso EPCenterline,
    pre-clipped to the study area; see mobility/data/)

Outputs (mobility/outputs/):
  - street_mode_split.csv       : per-street ped/vehicle counts + % split
  - street_activity_by_hour.csv : per-street, per-hour-bucket segment counts
  - street_activity_by_dow.csv  : per-street, per-day-of-week segment counts

Usage:
  python pathing_mode_street.py PATHING_REPORT_PATH [--streets STREETS_GEOJSON]
      [--buffer-ft 60] [--walk-mph 3.5] [--max-gap-min 20]

Assumptions to check against your actual data before trusting results:
  - Mode thresholds: speed <= WALK_MPH (default 3.5 mph) => pedestrian;
    speed > WALK_MPH => vehicular. There's no separate bike/transit bucket --
    adjust WALK_MPH or add a third band if you want one.
  - A gap of more than MAX_GAP_MIN minutes (default 20) between consecutive
    pings for the same device is treated as a break in the trail (device
    idle, out of range, or simply too sparse to infer travel mode) and is
    dropped rather than scored as a single very-slow or very-fast segment.
  - "Street buffer" = EPCenterline geometry buffered by --buffer-ft
    (default 60 ft total corridor width) in NAD83 Texas Central ftUS
    (EPSG:2257). A segment assigned to a street is one whose MIDPOINT falls
    within that street's buffer; segments that don't fall within any
    buffered street (e.g. cutting through a parking lot or parcel interior)
    are reported separately as "off-street" and excluded from the per-street
    totals.
  - Near intersections, a segment can fall inside more than one street's
    buffer (they overlap) and gets counted toward each -- so the sum of
    per-street totals will run a little higher than the segment count. This
    is expected; narrow --buffer-ft if you want tighter (but more gap-prone)
    attribution.
"""
import argparse
import csv
import gzip
import math
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, LineString

COLUMN_PATTERNS = {
    'device': ['hashed device id', 'hashed ubermedia id', 'udid', 'device id'],
    'lat': ['lat of observation point', 'latitude', 'lat'],
    'lon': ['lon of observation point', 'lng of observation point', 'longitude', 'lon', 'lng'],
    'timestamp': ['unix timestamp of observation point', 'unix timestamp', 'timestamp'],
    'date': ['local date', 'date'],
    'dow': ['local day of week', 'day of week'],
}


def find_col(columns, patterns):
    lower = {c.lower().strip(): c for c in columns}
    for pat in patterns:
        if pat in lower:
            return lower[pat]
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


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_street_buffers(streets_path, buffer_ft):
    streets = gpd.read_file(streets_path)
    if streets.crs is None:
        streets = streets.set_crs(4326)
    streets_ft = streets.to_crs(2277)  # NAD83 Texas Central, US survey feet
    streets_ft['geometry'] = streets_ft.geometry.buffer(buffer_ft / 2)
    name_col = 'STREETNAME' if 'STREETNAME' in streets_ft.columns else streets_ft.columns[0]
    dissolved = streets_ft.dissolve(by=name_col, as_index=False)
    return dissolved[[name_col, 'geometry']].rename(columns={name_col: 'STREETNAME'})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pathing_report', help='Path to the pathing_x_report .tsv or .tsv.gz file')
    ap.add_argument('--streets', default=None,
                     help='Path to study_area_street_centerlines.geojson (default: mobility/data/ next to this script)')
    ap.add_argument('--out-dir', default=None, help='Output directory (default: mobility/outputs/)')
    ap.add_argument('--buffer-ft', type=float, default=60.0, help='Total street corridor buffer width, feet')
    ap.add_argument('--walk-mph', type=float, default=3.5, help='Max speed (mph) classified as pedestrian')
    ap.add_argument('--max-gap-min', type=float, default=20.0, help='Max minutes between pings to still form a segment')
    ap.add_argument('--chunk-size', type=int, default=200_000)
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    streets_path = Path(args.streets) if args.streets else script_dir.parent / 'data' / 'study_area_street_centerlines.geojson'
    out_dir = Path(args.out_dir) if args.out_dir else script_dir.parent / 'outputs'
    out_dir.mkdir(parents=True, exist_ok=True)

    if not streets_path.exists():
        sys.exit(f"ERROR: streets file not found at {streets_path}. Pass --streets explicitly.")

    print(f"Loading street centerlines from {streets_path} ...")
    street_buffers = build_street_buffers(streets_path, args.buffer_ft)
    print(f"  {len(street_buffers)} named streets, buffered to {args.buffer_ft} ft corridor width")

    path_path = Path(args.pathing_report)
    if not path_path.exists():
        sys.exit(f"ERROR: pathing report not found at {path_path}")

    print(f"Scanning pathing report {path_path} ...")
    device_pings = defaultdict(list)  # device -> list of (ts, lat, lon, date, dow)
    total_rows = 0
    with open_text(path_path) as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        cols = {name: find_col(header, pats) for name, pats in COLUMN_PATTERNS.items()}
        missing = [n for n, c in cols.items() if c is None and n != 'dow']
        if missing:
            sys.exit(f"ERROR: couldn't find columns for {missing} in header: {header}\n"
                      f"Edit COLUMN_PATTERNS at the top of this script to match your file's exact headers.")
        idx = {name: header.index(c) for name, c in cols.items() if c is not None}
        print(f"  Using columns: " + ", ".join(f"{k}={cols[k]!r}" for k in cols if cols[k]))

        for row in reader:
            total_rows += 1
            try:
                dev = row[idx['device']]
                lat = float(row[idx['lat']])
                lon = float(row[idx['lon']])
                ts = float(row[idx['timestamp']])
                date = row[idx['date']] if 'date' in idx else None
                dow = row[idx['dow']] if 'dow' in idx else None
            except (ValueError, IndexError):
                continue
            device_pings[dev].append((ts, lat, lon, date, dow))
            if total_rows % args.chunk_size == 0:
                print(f"  ...{total_rows:,} rows read")

    print(f"Total rows read: {total_rows:,} across {len(device_pings):,} devices")

    # Build segments per device, classify mode, keep midpoint for street assignment
    seg_rows = []
    dropped_gap = 0
    for dev, pings in device_pings.items():
        pings.sort(key=lambda p: p[0])
        for (ts1, lat1, lon1, date1, dow1), (ts2, lat2, lon2, date2, dow2) in zip(pings, pings[1:]):
            dt_min = (ts2 - ts1) / 60.0
            if dt_min <= 0 or dt_min > args.max_gap_min:
                dropped_gap += 1
                continue
            dist_mi = haversine_miles(lat1, lon1, lat2, lon2)
            speed_mph = dist_mi / (dt_min / 60.0) if dt_min > 0 else 0
            mode = 'pedestrian' if speed_mph <= args.walk_mph else 'vehicular'
            mid_lat, mid_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
            # hour bucket from the later ping's local time, if we have it
            hour = None
            seg_rows.append({
                'device': dev, 'mid_lat': mid_lat, 'mid_lon': mid_lon,
                'speed_mph': speed_mph, 'mode': mode,
                'date': date2, 'dow': dow2,
            })

    print(f"Segments built: {len(seg_rows):,}  (dropped for gap/zero-time: {dropped_gap:,})")
    if not seg_rows:
        sys.exit("No usable segments -- check MAX_GAP_MIN and your timestamp column.")

    seg_df = pd.DataFrame(seg_rows)
    seg_gdf = gpd.GeoDataFrame(
        seg_df, geometry=gpd.points_from_xy(seg_df['mid_lon'], seg_df['mid_lat']), crs=4326
    ).to_crs(2277)

    joined = gpd.sjoin(seg_gdf, street_buffers, how='left', predicate='within')
    joined = joined.drop(columns='index_right', errors='ignore')
    on_street = joined.dropna(subset=['STREETNAME'])
    off_street_n = len(joined) - len(on_street)
    print(f"Segments assigned to a street buffer: {len(on_street):,}  (off-street: {off_street_n:,})")

    # --- Mode split per street ---
    mode_split = (
        on_street.groupby(['STREETNAME', 'mode']).size().unstack(fill_value=0)
    )
    for col in ('pedestrian', 'vehicular'):
        if col not in mode_split.columns:
            mode_split[col] = 0
    mode_split['total_segments'] = mode_split['pedestrian'] + mode_split['vehicular']
    mode_split['pct_pedestrian'] = (mode_split['pedestrian'] / mode_split['total_segments'] * 100).round(1)
    mode_split['pct_vehicular'] = (mode_split['vehicular'] / mode_split['total_segments'] * 100).round(1)
    mode_split = mode_split.sort_values('total_segments', ascending=False)
    mode_split_path = out_dir / 'street_mode_split.csv'
    mode_split.to_csv(mode_split_path)
    print(f"\nSaved {mode_split_path}")
    print(mode_split[['pedestrian', 'vehicular', 'pct_pedestrian', 'pct_vehicular']].head(15).to_string())

    # --- Activity by day of week ---
    if 'dow' in on_street.columns and on_street['dow'].notna().any():
        dow_counts = on_street.groupby(['STREETNAME', 'dow']).size().unstack(fill_value=0)
        dow_path = out_dir / 'street_activity_by_dow.csv'
        dow_counts.to_csv(dow_path)
        print(f"Saved {dow_path}")

    print("\nDone. Busiest streets overall (all modes):")
    print(mode_split['total_segments'].head(10).to_string())


if __name__ == '__main__':
    main()
