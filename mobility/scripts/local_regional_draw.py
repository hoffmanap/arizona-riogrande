"""
Local vs. regional draw analysis for the Five Points / Sunset Heights study area.

Inputs (SafeGraph/Ubermedia-style mobility deliverables, polygon = "location_1" = subarea):
  - *resident_worker_report.tsv.gz : per-device resident/worker flag + home (CEL) / work (CDL) location
  - *expanded_cel_cdl_detailed_report_summary.tsv.gz : pre-aggregated distance-from-home/work buckets
    (inside *expanded_cel_cdl_detailed_report.zip if not already extracted)

Answers: what share of visitation is local (lives/works nearby) vs a regional draw
(commuting/traveling from farther away), to calibrate how much the corridor should
plan for neighborhood-serving vs. regional-draw commercial uses.

Usage:
    python local_regional_draw.py [DATA_DIR]

    DATA_DIR defaults to the current working directory. Point it at the folder
    holding the downloaded report files (e.g. your "Arizona-Rio Grande" folder) --
    the script finds the right files by name pattern, so exact filenames/paths
    don't matter. If only the _report.zip is present, it's auto-extracted
    alongside itself.
"""
import gzip
import csv
import sys
import zipfile
from pathlib import Path
from collections import Counter

def find_one(data_dir, pattern):
    matches = sorted(data_dir.glob(pattern))
    if not matches:
        return None
    return matches[0]

def locate_inputs(data_dir):
    resident_worker = find_one(data_dir, "*resident_worker_report*.tsv.gz")
    summary = find_one(data_dir, "*expanded_cel_cdl_detailed_report_summary*.tsv.gz")

    if summary is None:
        # maybe it's still zipped -- extract it alongside the zip
        zpath = find_one(data_dir, "*expanded_cel_cdl_detailed_report*.zip")
        if zpath is not None:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(zpath.parent)
            summary = find_one(data_dir, "*expanded_cel_cdl_detailed_report_summary*.tsv.gz")

    missing = []
    if resident_worker is None:
        missing.append("*resident_worker_report*.tsv.gz")
    if summary is None:
        missing.append("*expanded_cel_cdl_detailed_report_summary*.tsv.gz (or the .zip it ships in)")
    if missing:
        print(f"ERROR: couldn't find these files under {data_dir}:")
        for m in missing:
            print(f"  - {m}")
        print("Pass the folder containing the downloaded report files as an argument, e.g.:")
        print(f'  python {Path(__file__).name} "C:\\Users\\Angelica\\OneDrive\\Near Data\\Arizona-Rio Grande"')
        sys.exit(1)

    return resident_worker, summary

def read_tsv_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
        yield from csv.DictReader(f, delimiter='\t')

data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
RESIDENT_WORKER, SUMMARY = locate_inputs(data_dir)
print(f"Using resident/worker report: {RESIDENT_WORKER}")
print(f"Using CEL/CDL summary:        {SUMMARY}\n")

# --- Resident / worker flags ---
resident_counts = Counter()
worker_counts = Counter()
both = 0
n = 0
for row in read_tsv_gz(RESIDENT_WORKER):
    n += 1
    r = row['Resident'].strip().lower()
    w = row['Worker'].strip().lower()
    resident_counts[r] += 1
    worker_counts[w] += 1
    if r == 'true' and w == 'true':
        both += 1

print(f"Total unique devices in resident/worker report: {n}")
print("Resident flag:", dict(resident_counts))
print("Worker flag:", dict(worker_counts))
print(f"Both resident AND worker (live+work in/near subarea): {both}")

# --- Distance-bucket summary (pre-aggregated by vendor) ---
print("\n--- Distance from home (CEL) / work (CDL) buckets ---")
rows = list(read_tsv_gz(SUMMARY))
dist_rows = [r for r in rows if r['Type'] in ('CEL Distance', 'CDL Distance')]
for r in dist_rows:
    print(f"{r['Type']:15s} {r['Bucket']:35s} visitors={r['Visitor Count']:>10s} ({r['Visitor Percentage']:>7s})  visits={r['Visit Count']:>8s} ({r['Visit Percentage']:>7s})")

# --- Roll up into Local / Citywide / Regional / Distant bands ---
def pct(bucket_row):
    return float(bucket_row['Visit Percentage'].strip('%'))

def rollup(dist_type, label_map):
    bucket = {r['Bucket']: r for r in dist_rows if r['Type'] == dist_type}
    bands = {}
    for band_label, bucket_names in label_map.items():
        bands[band_label] = sum(pct(bucket[b]) for b in bucket_names if b in bucket)
    return bands

BAND_DEFS = {
    "Local (<3 mi)": ["Lives within 1 mile of location", "Lives between 1 and 2 miles", "Lives between 2 and 3 miles"],
    "Citywide (3-10 mi)": ["Lives between 3 and 5 miles", "Lives between 5 and 10 miles"],
    "Regional metro (10-25 mi)": ["Lives between 10 and 25 miles"],
    "Distant/out-of-town (>25 mi)": ["Lives between 25 and 50 miles", "Lives between 50 and 100 miles", "Lives >100 miles away"],
}
BAND_DEFS_WORK = {k: [b.replace("Lives", "Works") for b in v] for k, v in BAND_DEFS.items()}

print("\n--- Local / citywide / regional rollup, by % of visits ---")
home_bands = rollup('CEL Distance', BAND_DEFS)
work_bands = rollup('CDL Distance', BAND_DEFS_WORK)
for label in BAND_DEFS:
    print(f"{label:32s} home={home_bands[label]:5.1f}%   work={work_bands[label]:5.1f}%")
