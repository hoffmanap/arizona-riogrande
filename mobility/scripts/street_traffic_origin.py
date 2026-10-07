#!/usr/bin/env python3
"""
street_traffic_origin.py

Classifies each street's measured movement as coming from a LOCAL device
(home within a configurable radius of the study area) or a DISTANT device
(regional/out-of-town), on top of the existing pedestrian/vehicular split --
so a street's traffic can be read as "local foot traffic", "regional
pass-through driving", etc., not just a raw volume number.

WHY THIS SCRIPT EXISTS
-----------------------
pathing_mode_street.py already assigns every device movement segment to a
street and a travel mode (pedestrian/vehicular), but it has no idea whether
that device lives nearby or is passing through from across the metro.
resident_worker_report.tsv.gz (or expanded_cel_cdl_detailed_report's CEL
summary) has each device's home (CEL) distance from the study area, but no
street attribution. This script joins the two by device ID, same pattern as
dwell_by_category.py: neither file alone answers the question, the join does.

METHOD
------
1. Re-run the same segment reconstruction + street/mode assignment as
   pathing_mode_street.py (device, street, mode) from the pathing_x_report.
2. Load each device's home distance band from resident_worker_report.tsv.gz
   (or, if you only have the expanded_cel_cdl per-visit file, pass that with
   --cel-cdl instead -- see --help).
3. Bucket each device as LOCAL (home within --local-miles, default 3) or
   DISTANT (home farther than that, or home not resolved -- the latter are
   reported separately, not silently folded into either bucket).
4. Aggregate, per street and per mode, the segment counts by origin bucket.

USAGE
-----
    python street_traffic_origin.py PATHING_REPORT_PATH RESIDENT_WORKER_PATH [--streets PATH] [--out PATH] [--local-miles 3]

OUTPUT
------
street_traffic_origin.csv with columns:
    STREETNAME, mode, origin, n_segments, pct_of_street_mode
(mode is "pedestrian"/"vehicular"; origin is "local"/"distant"/"unresolved")
"""
import sys
import argparse
import gzip
import csv
import json
import math
from pathlib import Path
from collections import defaultdict, Counter


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


def find_one(data_dir: Path, pattern: str):
    matches = sorted(data_dir.glob(pattern))
    return matches[0] if matches else None


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_device_origin(resident_worker_path: Path, local_miles: float, study_lat: float, study_lon: float):
    """Returns {UDID: 'local'|'distant'} using CEL lat/lon distance from the study area."""
    origin = {}
    with open_maybe_gz(resident_worker_path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames
        udid_col = find_col(fieldnames, ["device id", "udid", "deviceid"])
        lat_col = find_col(fieldnames, ["cel lat"])
        lon_col = find_col(fieldnames, ["cel lng", "cel lon"])
        if not (udid_col and lat_col and lon_col):
            sys.exit(f"ERROR: could not find device/CEL-lat/CEL-lon columns. Found: {fieldnames}")
        print(f"Using columns: device={udid_col}, cel_lat={lat_col}, cel_lon={lon_col}")
        for row in reader:
            try:
                lat, lon = float(row[lat_col]), float(row[lon_col])
            except (TypeError, ValueError):
                continue
            dist = haversine_miles(study_lat, study_lon, lat, lon)
            origin[row[udid_col]] = "local" if dist <= local_miles else "distant"
    return origin


def load_streets(streets_path: Path):
    with open(streets_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    streets = []
    for feat in data["features"]:
        coords = feat["geometry"]["coordinates"]
        if feat["geometry"]["type"] != "LineString":
            continue
        streets.append({"name": feat["properties"]["STREETNAME"], "coords": coords})
    return streets


def point_segment_dist_miles(lat, lon, lat1, lon1, lat2, lon2):
    # approximate: project onto the segment in a local equirectangular frame, then haversine to closest point
    mlat = math.radians((lat1 + lat2) / 2)
    kx = 69.172 * math.cos(mlat)  # miles per degree longitude at this latitude
    ky = 69.0  # miles per degree latitude
    x, y = lon * kx, lat * ky
    x1, y1 = lon1 * kx, lat1 * ky
    x2, y2 = lon2 * kx, lat2 * ky
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px, py = x1 + t * dx, y1 + t * dy
    return math.hypot(x - px, y - py)


def nearest_street(lat, lon, streets, buffer_miles):
    best_name, best_dist = None, buffer_miles
    for s in streets:
        coords = s["coords"]
        for i in range(len(coords) - 1):
            lon1, lat1 = coords[i]
            lon2, lat2 = coords[i + 1]
            d = point_segment_dist_miles(lat, lon, lat1, lon1, lat2, lon2)
            if d < best_dist:
                best_dist, best_name = d, s["name"]
    return best_name


WALK_MPH_DEFAULT = 3.5
MAX_GAP_MIN_DEFAULT = 20


def trip_mode_hops(pings, max_gap_min, walk_mph):
    """pings: sorted list of (timestamp, lat, lon) for one device.

    Groups consecutive pings into TRIPS (bounded by max_gap_min, same threshold
    already used to avoid stitching together unrelated movements), then
    classifies each trip's mode from its OVERALL speed -- total trip distance
    divided by total trip time -- instead of each hop's own instantaneous
    speed. A momentary slowdown (a stoplight, a block of congestion) gets
    averaged into the trip's overall pace rather than flipping that one hop's
    mode on its own; a genuine mode change (park the car, then walk) still
    splits correctly, since that's exactly what a >max_gap_min pause between
    pings already represents as a new trip.

    Yields (lat1, lon1, lat2, lon2, mode) per hop.
    """
    if len(pings) < 2:
        return
    trips = []
    current = [pings[0]]
    for prev, cur in zip(pings, pings[1:]):
        dt_min = (cur[0] - prev[0]) / 60.0
        if dt_min <= 0 or dt_min > max_gap_min:
            if len(current) >= 2:
                trips.append(current)
            current = [cur]
        else:
            current.append(cur)
    if len(current) >= 2:
        trips.append(current)

    for trip in trips:
        total_dist = 0.0
        total_time_min = 0.0
        for a, b in zip(trip, trip[1:]):
            total_dist += haversine_miles(a[1], a[2], b[1], b[2])
            total_time_min += (b[0] - a[0]) / 60.0
        if total_time_min <= 0:
            continue
        trip_speed_mph = total_dist / (total_time_min / 60.0)
        mode = "pedestrian" if trip_speed_mph <= walk_mph else "vehicular"
        for a, b in zip(trip, trip[1:]):
            yield a[1], a[2], b[1], b[2], mode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pathing_report", type=Path)
    ap.add_argument("resident_worker", type=Path)
    ap.add_argument("--streets", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--local-miles", type=float, default=3.0)
    ap.add_argument("--walk-mph", type=float, default=WALK_MPH_DEFAULT)
    ap.add_argument("--max-gap-min", type=float, default=MAX_GAP_MIN_DEFAULT)
    ap.add_argument("--buffer-ft", type=float, default=60.0)
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    streets_path = args.streets or find_one(script_dir.parent / "data", "*street_centerlines*.geojson")
    if not streets_path or not Path(streets_path).exists():
        sys.exit("ERROR: could not find study_area_street_centerlines.geojson -- pass --streets explicitly")

    print(f"Using pathing report:    {args.pathing_report}")
    print(f"Using resident/worker:   {args.resident_worker}")
    print(f"Using streets file:      {streets_path}")

    streets = load_streets(Path(streets_path))
    print(f"Loaded {len(streets)} street segments")

    # study-area centroid for distance-from-study-area calc (mean of all street vertices)
    all_lats, all_lons = [], []
    for s in streets:
        for lon, lat in s["coords"]:
            all_lats.append(lat)
            all_lons.append(lon)
    study_lat, study_lon = sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)

    device_origin = load_device_origin(args.resident_worker, args.local_miles, study_lat, study_lon)
    print(f"Resolved home-origin for {len(device_origin):,} devices")

    buffer_miles = args.buffer_ft / 5280.0

    delim = sniff_delimiter(args.pathing_report)
    with open_maybe_gz(args.pathing_report) as f:
        reader = csv.DictReader(f, delimiter=delim)
        fieldnames = reader.fieldnames
        device_col = find_col(fieldnames, ["hashed device id", "device id", "udid"])
        lat_col = find_col(fieldnames, ["latitude", "lat"])
        lon_col = find_col(fieldnames, ["longitude", "lon", "lng"])
        ts_col = find_col(fieldnames, ["unix timestamp", "timestamp"])
        if not (device_col and lat_col and lon_col and ts_col):
            sys.exit(f"ERROR: could not find device/lat/lon/timestamp columns. Found: {fieldnames}")
        print(f"Using columns: device={device_col}, lat={lat_col}, lon={lon_col}, ts={ts_col}")

        device_pings = defaultdict(list)
        n_rows = 0
        for row in reader:
            n_rows += 1
            try:
                lat, lon, ts = float(row[lat_col]), float(row[lon_col]), float(row[ts_col])
            except (TypeError, ValueError):
                continue
            device_pings[row[device_col]].append((ts, lat, lon))
            if n_rows % 500000 == 0:
                print(f"  ...read {n_rows:,} rows")
    print(f"Read {n_rows:,} pathing rows across {len(device_pings):,} devices")

    counts = Counter()  # (street, mode, origin) -> n_segments
    n_segments = 0
    for udid, pings in device_pings.items():
        pings.sort(key=lambda p: p[0])
        origin = device_origin.get(udid, None)
        origin_label = origin if origin else "unresolved"
        for lat1, lon1, lat2, lon2, mode in trip_mode_hops(pings, args.max_gap_min, args.walk_mph):
            mid_lat, mid_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
            street = nearest_street(mid_lat, mid_lon, streets, buffer_miles)
            if not street:
                continue
            counts[(street, mode, origin_label)] += 1
            n_segments += 1

    print(f"Classified {n_segments:,} segments across {len({k[0] for k in counts}):,} streets")
    if n_segments == 0:
        sys.exit("ERROR: no segments classified -- check column detection above and your --buffer-ft")

    out_path = args.out or (args.pathing_report.parent / "street_traffic_origin.csv")
    # totals per (street, mode) for percent-of-mode denominators
    mode_totals = Counter()
    for (street, mode, origin), n in counts.items():
        mode_totals[(street, mode)] += n

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["STREETNAME", "mode", "origin", "n_segments", "pct_of_street_mode"])
        for (street, mode, origin), n in sorted(counts.items()):
            total = mode_totals[(street, mode)]
            w.writerow([street, mode, origin, n, round(100 * n / total, 1) if total else 0])

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
