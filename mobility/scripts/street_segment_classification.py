#!/usr/bin/env python3 
"""
street_segment_classification.py

Classifies each individual street SEGMENT -- the actual block face between two
cross streets, not the whole named street -- as one of:
    LOCAL        -- low traffic, low business visits: mostly residential block
    PASS-THROUGH -- high traffic, low business visits relative to that traffic:
                    cars/pedestrians moving through without stopping
    DESTINATION  -- business visits high relative to traffic on that block:
                    people are stopping here specifically

WHY THIS EXISTS
---------------
pathing_mode_street.py and street_traffic_origin.py both collapse all segments
of e.g. "MISSOURI" into one street-wide total. That hides exactly the thing a
rezoning recommendation needs: a street can be a quiet residential block for
six blocks and a destination commercial block for two. The subarea's street
centerline file is already split into block-level features (175 features
across 21 streets, ~9 blocks per street on average) -- this script uses THAT
granularity instead of collapsing to street name.

METHOD
------
1. Load the block-level centerline features directly (no re-aggregation by
   name). For each one, label its two cross streets by finding the nearest
   OTHER street's segment to each endpoint (within --cross-tol-ft).
2. Re-run the same pathing_x_report segment reconstruction as
   pathing_mode_street.py / street_traffic_origin.py, but assign each movement
   segment to the nearest BLOCK FEATURE (by id), not the nearest street name.
3. Join business visits directly by geometry: each SafeGraph place's
   lat/lon (no more address-string matching) to its nearest block feature.
   Lessors of Real Estate is EXCLUDED from the visit total used for
   classification -- that category mostly measures residents dwelling at
   home, not commercial draw, and including it would inflate "destination"
   scores for apartment-heavy blocks that aren't commercial at all (same
   caveat flagged for dwell-time-by-category in mobility.html).
4. Classify each block by comparing its traffic volume and (non-Lessors)
   visits-per-unit-traffic to the subarea-wide median of each, the same
   median-split logic used in visits.html's quadrant tool, so a block's
   label is relative to this subarea, not an absolute threshold:
       traffic >= median_traffic  &  visits/traffic >= median_ratio  -> DESTINATION
       traffic >= median_traffic  &  visits/traffic <  median_ratio  -> PASS-THROUGH
       traffic <  median_traffic                                     -> LOCAL
   (a low-traffic block with high visits/traffic -- a "hidden draw" -- is
   still flagged LOCAL here since its absolute traffic is low, but is noted
   separately in the output so it isn't lost.)

USAGE
-----
    python street_segment_classification.py PATHING_REPORT_PATH RESIDENT_WORKER_PATH \
        [--streets PATH] [--places PATH] [--visits PATH] [--out PATH] \
        [--cross-tol-ft 60] [--local-miles 3]

OUTPUT
------
street_segment_classification.csv with columns:
    STREETNAME, segment_id, cross_street_a, cross_street_b, n_segments_total,
    n_segments_pedestrian, n_segments_vehicular, pct_local_of_resolved,
    n_places_excl_lessors, visits_excl_lessors, visits_per_traffic,
    classification
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


def load_device_origin(resident_worker_path, local_miles, study_lat, study_lon):
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


def load_segments(streets_path):
    with open(streets_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    segs = []
    for i, feat in enumerate(data["features"]):
        if feat["geometry"]["type"] != "LineString":
            continue
        coords = feat["geometry"]["coordinates"]
        segs.append({
            "id": i,
            "name": feat["properties"]["STREETNAME"],
            "coords": coords,
            "end_a": (coords[0][1], coords[0][0]),   # (lat, lon)
            "end_b": (coords[-1][1], coords[-1][0]),
        })
    return segs


def point_segment_dist_miles(lat, lon, lat1, lon1, lat2, lon2):
    mlat = math.radians((lat1 + lat2) / 2)
    kx = 69.172 * math.cos(mlat)
    ky = 69.0
    x, y = lon * kx, lat * ky
    x1, y1 = lon1 * kx, lat1 * ky
    x2, y2 = lon2 * kx, lat2 * ky
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px, py = x1 + t * dx, y1 + t * dy
    return math.hypot(x - px, y - py)


def nearest_segment(lat, lon, segs, buffer_miles):
    best_id, best_dist = None, buffer_miles
    for s in segs:
        coords = s["coords"]
        for i in range(len(coords) - 1):
            lon1, lat1 = coords[i]
            lon2, lat2 = coords[i + 1]
            d = point_segment_dist_miles(lat, lon, lat1, lon1, lat2, lon2)
            if d < best_dist:
                best_dist, best_id = d, s["id"]
    return best_id


def label_cross_streets(segs, tol_miles):
    """For each segment, find the nearest OTHER-named segment to each endpoint."""
    cross = {}
    for s in segs:
        labels = []
        for end in (s["end_a"], s["end_b"]):
            best_name, best_dist = None, tol_miles
            for other in segs:
                if other["id"] == s["id"] or other["name"] == s["name"]:
                    continue
                for pt in (other["end_a"], other["end_b"]):
                    d = haversine_miles(end[0], end[1], pt[0], pt[1])
                    if d < best_dist:
                        best_dist, best_name = d, other["name"]
            labels.append(best_name or "(subarea edge)")
        cross[s["id"]] = tuple(labels)
    return cross


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
    ap.add_argument("--places", type=Path, default=None)
    ap.add_argument("--visits", type=Path, default=None, help="place_visit_density.csv (unique_visits per PLACEKEY)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--cross-tol-ft", type=float, default=60.0)
    ap.add_argument("--local-miles", type=float, default=3.0)
    ap.add_argument("--walk-mph", type=float, default=WALK_MPH_DEFAULT)
    ap.add_argument("--max-gap-min", type=float, default=MAX_GAP_MIN_DEFAULT)
    ap.add_argument("--buffer-ft", type=float, default=60.0)
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir.parent / "data"
    streets_path = args.streets or find_one(data_dir, "*street_centerlines*.geojson")
    places_path = args.places or find_one(data_dir, "*safegraph_places*.geojson")
    visits_path = args.visits or find_one(data_dir, "place_visit_density.csv") or find_one(data_dir, "*visit_density*.csv")
    if not streets_path or not Path(streets_path).exists():
        sys.exit("ERROR: could not find study_area_street_centerlines.geojson -- pass --streets explicitly")
    if not places_path or not Path(places_path).exists():
        sys.exit("ERROR: could not find SafeGraph places geojson -- pass --places explicitly")
    if not visits_path or not Path(visits_path).exists():
        sys.exit("ERROR: could not find place_visit_density.csv -- pass --visits explicitly")

    print(f"Using pathing report:    {args.pathing_report}")
    print(f"Using resident/worker:   {args.resident_worker}")
    print(f"Using streets file:      {streets_path}")
    print(f"Using places file:       {places_path}")
    print(f"Using visits file:       {visits_path}")

    segs = load_segments(Path(streets_path))
    print(f"Loaded {len(segs)} block-level street segments across "
          f"{len({s['name'] for s in segs})} streets")

    cross_tol_miles = args.cross_tol_ft / 5280.0
    cross = label_cross_streets(segs, cross_tol_miles)

    all_lats = [lat for s in segs for lon, lat in s["coords"]]
    all_lons = [lon for s in segs for lon, lat in s["coords"]]
    study_lat, study_lon = sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)

    device_origin = load_device_origin(args.resident_worker, args.local_miles, study_lat, study_lon)
    print(f"Resolved home-origin for {len(device_origin):,} devices")

    # ---- business visits per segment, excluding Lessors of Real Estate ----
    visits_by_placekey = {}
    with open(visits_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                visits_by_placekey[row["PLACEKEY"]] = float(row["unique_visits"])
            except (KeyError, ValueError):
                continue

    buffer_miles = args.buffer_ft / 5280.0
    seg_visits = Counter()
    seg_places = Counter()
    with open(places_path, "r", encoding="utf-8") as f:
        places = json.load(f)["features"]
    n_excluded_lessors = 0
    for feat in places:
        props = feat["properties"]
        if props.get("TOP_CATEGORY") == "Lessors of Real Estate":
            n_excluded_lessors += 1
            continue
        pk = props.get("PLACEKEY")
        v = visits_by_placekey.get(pk)
        if v is None:
            continue
        try:
            lat, lon = float(props["LATITUDE"]), float(props["LONGITUDE"])
        except (TypeError, ValueError, KeyError):
            continue
        sid = nearest_segment(lat, lon, segs, buffer_miles)
        if sid is None:
            continue
        seg_visits[sid] += v
        seg_places[sid] += 1
    print(f"Joined visits for {sum(seg_places.values())} places to segments "
          f"({n_excluded_lessors} Lessors-of-Real-Estate places excluded from visit totals)")

    # ---- pathing segments -> nearest block feature + mode + origin ----
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

    counts = Counter()  # (segment_id, mode, origin) -> n
    n_segments = 0
    for udid, pings in device_pings.items():
        pings.sort(key=lambda p: p[0])
        origin = device_origin.get(udid, None)
        origin_label = origin if origin else "unresolved"
        for lat1, lon1, lat2, lon2, mode in trip_mode_hops(pings, args.max_gap_min, args.walk_mph):
            mid_lat, mid_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
            sid = nearest_segment(mid_lat, mid_lon, segs, buffer_miles)
            if sid is None:
                continue
            counts[(sid, mode, origin_label)] += 1
            n_segments += 1

    print(f"Classified {n_segments:,} movement segments across "
          f"{len({k[0] for k in counts}):,} block features")
    if n_segments == 0:
        sys.exit("ERROR: no segments classified -- check column detection above and your --buffer-ft")

    # ---- aggregate per block feature ----
    seg_total = Counter()
    seg_ped = Counter()
    seg_veh = Counter()
    seg_local = Counter()
    seg_resolved = Counter()
    for (sid, mode, origin), n in counts.items():
        seg_total[sid] += n
        if mode == "pedestrian":
            seg_ped[sid] += n
        else:
            seg_veh[sid] += n
        if origin in ("local", "distant"):
            seg_resolved[sid] += n
            if origin == "local":
                seg_local[sid] += n

    seg_ids = sorted(seg_total.keys())
    traffics = [seg_total[sid] for sid in seg_ids]
    ratios = {}
    for sid in seg_ids:
        v = seg_visits.get(sid, 0)
        t = seg_total[sid]
        ratios[sid] = v / t if t else 0.0

    def median(arr):
        s = sorted(arr)
        n = len(s)
        return n and (s[(n - 1) // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

    med_traffic = median(traffics)
    med_ratio = median([ratios[sid] for sid in seg_ids])
    print(f"Median block traffic: {med_traffic:.0f} segments; median visits-per-traffic ratio: {med_ratio:.4f}")

    out_path = args.out or (args.pathing_report.parent / "street_segment_classification.csv")
    seg_by_id = {s["id"]: s for s in segs}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["STREETNAME", "segment_id", "cross_street_a", "cross_street_b",
                    "n_segments_total", "n_segments_pedestrian", "n_segments_vehicular",
                    "pct_local_of_resolved", "n_places_excl_lessors", "visits_excl_lessors",
                    "visits_per_traffic", "classification", "note"])
        for sid in seg_ids:
            s = seg_by_id[sid]
            ca, cb = cross.get(sid, ("", ""))
            total = seg_total[sid]
            resolved = seg_resolved[sid]
            pct_local = round(100 * seg_local[sid] / resolved, 1) if resolved else None
            visits = seg_visits.get(sid, 0)
            n_places = seg_places.get(sid, 0)
            ratio = ratios[sid]
            note = ""
            if total >= med_traffic and ratio >= med_ratio:
                label = "DESTINATION"
            elif total >= med_traffic:
                label = "PASS-THROUGH"
            else:
                label = "LOCAL"
                if ratio >= med_ratio and visits > 0:
                    note = "hidden draw -- high visits-per-traffic despite low absolute traffic"
            w.writerow([s["name"], sid, ca, cb, total, seg_ped[sid], seg_veh[sid],
                        pct_local if pct_local is not None else "", n_places,
                        round(visits, 1), round(ratio, 4), label, note])

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
