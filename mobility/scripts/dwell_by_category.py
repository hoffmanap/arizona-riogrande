#!/usr/bin/env python3
"""
dwell_by_category.py

Joins the per-device dwell_time report to each device's dominant SafeGraph
place category, so dwell time can be broken down (and toggled) by category
instead of looked at as a single subarea-wide distribution.

WHY THIS SCRIPT EXISTS
-----------------------
dwell_time_report.tsv.gz is keyed only by (Study Polygon, UDID) -- it has no
place or category info. place_visit_density.csv (built by pin_to_places.py)
is aggregated to the place level -- it has no per-device info. Neither file
alone can answer "how does dwell time differ by category" -- you need the
raw pin_report (device, lat/lon, timestamp) to assign each device to the
place(s) it visited, then join that back to the dwell_time report by UDID.
This script does that join.

METHOD
------
1. Load the pin_report and spatially join every ping to the SafeGraph place
   polygon it falls inside (same logic as pin_to_places.py).
2. For each device (UDID), find its dominant category = the TOP_CATEGORY of
   the place where it has the most pings. A device that visited multiple
   categories is credited only to its dominant one, to keep each device's
   dwell-time row from being double-counted across categories.
3. Join that dominant category onto the device's row in dwell_time_report.tsv.gz
   by UDID.
4. Bucket each device's "Avg Dwell-Time (in seconds)" into the same six
   buckets used elsewhere on the site (<5 min, 5-15, 15-30, 30-60, 1-2hr, 2hr+)
   and output counts + shares per category.

USAGE
-----
    python dwell_by_category.py PIN_REPORT_PATH DWELL_TIME_PATH [--places PATH] [--out PATH] [--top-n N]

    PIN_REPORT_PATH    the raw pin_report (device/lat/lon/timestamp pings), .tsv.gz or .csv.gz
    DWELL_TIME_PATH    the dwell_time report, .tsv.gz
    --places           path to el_paso_subarea_safegraph_places.geojson
                        (default: looks in a sibling "data" folder next to this script)
    --out              output CSV path (default: dwell_by_category.csv next to the input)
    --top-n            how many of the busiest categories to keep separately;
                        everything else is grouped into "Other" (default: 5)

OUTPUT
------
A CSV with columns: category, bucket, n_devices, share_pct -- one row per
(category x dwell-time bucket), ready to drive a toggle chart.
"""
import sys
import argparse
import gzip
import csv
import json
from pathlib import Path
from collections import defaultdict, Counter


def find_one(data_dir: Path, pattern: str):
    matches = sorted(data_dir.glob(pattern))
    return matches[0] if matches else None


def open_maybe_gz(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def sniff_delimiter(path: Path) -> str:
    with open_maybe_gz(path) as f:
        head = f.readline()
    return "\t" if head.count("\t") >= head.count(",") else ","


def find_col(fieldnames, patterns):
    for p in patterns:
        for fn in fieldnames:
            if p.lower() in fn.lower():
                return fn
    return None


def point_in_ring(x, y, ring):
    # standard ray-casting point-in-polygon test
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, geom):
    if geom["type"] == "Polygon":
        rings = geom["coordinates"]
        if not point_in_ring(x, y, rings[0]):
            return False
        for hole in rings[1:]:
            if point_in_ring(x, y, hole):
                return False
        return True
    if geom["type"] == "MultiPolygon":
        return any(point_in_polygon(x, y, {"type": "Polygon", "coordinates": poly}) for poly in geom["coordinates"])
    return False


def load_places(places_path: Path):
    with open(places_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    places = []
    for feat in data["features"]:
        props = feat["properties"]
        # bbox for a cheap pre-filter before the exact point-in-polygon test
        coords = feat["geometry"]["coordinates"]
        pts = []

        def walk(c):
            if isinstance(c[0], (int, float)):
                pts.append(c)
            else:
                for cc in c:
                    walk(cc)

        walk(coords)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        places.append({
            "placekey": props.get("PLACEKEY"),
            "category": props.get("TOP_CATEGORY") or "Unknown",
            "geometry": feat["geometry"],
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
        })
    return places


def assign_place(lon, lat, places):
    for p in places:
        minx, miny, maxx, maxy = p["bbox"]
        if minx <= lon <= maxx and miny <= lat <= maxy:
            if point_in_polygon(lon, lat, p["geometry"]):
                return p
    return None


BUCKETS = [
    ("<5 min", 0, 5),
    ("5-15 min", 5, 15),
    ("15-30 min", 15, 30),
    ("30-60 min", 30, 60),
    ("1-2 hr", 60, 120),
    ("2+ hr", 120, float("inf")),
]


def bucket_for(minutes):
    for name, lo, hi in BUCKETS:
        if lo <= minutes < hi:
            return name
    return BUCKETS[-1][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pin_report", type=Path)
    ap.add_argument("dwell_time", type=Path)
    ap.add_argument("--places", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--top-n", type=int, default=5)
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    places_path = args.places or find_one(script_dir.parent / "data", "*safegraph_places*.geojson")
    if not places_path or not Path(places_path).exists():
        sys.exit("ERROR: could not find el_paso_subarea_safegraph_places.geojson -- pass --places explicitly")

    print(f"Using pin report:   {args.pin_report}")
    print(f"Using dwell report: {args.dwell_time}")
    print(f"Using places file:  {places_path}")

    places = load_places(Path(places_path))
    print(f"Loaded {len(places)} place polygons")

    # --- Pass 1: read pin_report, tally pings per device per place ---
    delim = sniff_delimiter(args.pin_report)
    with open_maybe_gz(args.pin_report) as f:
        reader = csv.DictReader(f, delimiter=delim)
        fieldnames = reader.fieldnames
        device_col = find_col(fieldnames, ["device_id", "udid", "device id", "deviceid"])
        lat_col = find_col(fieldnames, ["latitude", "lat"])
        lon_col = find_col(fieldnames, ["longitude", "lon", "lng"])
        if not (device_col and lat_col and lon_col):
            sys.exit(f"ERROR: could not find device/lat/lon columns in pin_report. Found: {fieldnames}")
        print(f"Using columns: device={device_col}, lat={lat_col}, lon={lon_col}")

        device_place_counts = defaultdict(Counter)
        n_rows = 0
        for row in reader:
            n_rows += 1
            try:
                lat = float(row[lat_col])
                lon = float(row[lon_col])
            except (TypeError, ValueError):
                continue
            place = assign_place(lon, lat, places)
            if place:
                device_place_counts[row[device_col]][place["category"]] += 1
            if n_rows % 500000 == 0:
                print(f"  ...processed {n_rows:,} pings")
    print(f"Processed {n_rows:,} pings across {len(device_place_counts):,} devices with at least one place match")

    # dominant category per device
    device_category = {}
    for udid, counts in device_place_counts.items():
        device_category[udid] = counts.most_common(1)[0][0]

    # --- Pass 2: read dwell_time report, bucket by dominant category ---
    bucket_counts = defaultdict(Counter)
    n_matched = 0
    n_total = 0
    with gzip.open(args.dwell_time, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            n_total += 1
            udid = row.get("UDID")
            cat = device_category.get(udid)
            if not cat:
                continue
            try:
                avg_seconds = float(row["Avg Dwell-Time (in seconds)"])
            except (TypeError, ValueError):
                continue
            n_matched += 1
            bucket_counts[cat][bucket_for(avg_seconds / 60.0)] += 1

    print(f"Matched {n_matched:,} of {n_total:,} dwell_time rows to a device category")
    if n_matched == 0:
        sys.exit("ERROR: no dwell_time rows matched a device category -- check that UDIDs in both files use the same ID scheme")

    # keep top-N categories by total matched devices, group the rest as "Other"
    totals = {cat: sum(c.values()) for cat, c in bucket_counts.items()}
    top_cats = [c for c, _ in sorted(totals.items(), key=lambda kv: -kv[1])[: args.top_n]]
    other = Counter()
    final = {c: bucket_counts[c] for c in top_cats}
    for cat, counts in bucket_counts.items():
        if cat not in top_cats:
            other.update(counts)
    if other:
        final["Other"] = other

    out_path = args.out or (args.pin_report.parent / "dwell_by_category.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "bucket", "n_devices", "share_pct"])
        for cat, counts in final.items():
            total = sum(counts.values())
            for bucket_name, _, _ in BUCKETS:
                n = counts.get(bucket_name, 0)
                w.writerow([cat, bucket_name, n, round(100 * n / total, 1) if total else 0])

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
