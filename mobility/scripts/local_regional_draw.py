"""
Local vs. regional draw analysis for the Five Points / Sunset Heights study area.

Inputs (SafeGraph/Ubermedia-style mobility deliverables, polygon = "location_1" = subarea):
  - resident_worker_report.tsv.gz : per-device resident/worker flag + home (CEL) / work (CDL) location
  - expanded_cel_cdl_detailed_report_summary.tsv.gz : pre-aggregated distance-from-home/work buckets
  - expanded_cel_cdl_detailed_report_cel.tsv.gz / _cdl.tsv.gz : per-visit home/work location pings

Answers: what share of visitation is local (lives/works nearby) vs a regional draw
(commuting/traveling from farther away), to calibrate how much the corridor should
plan for neighborhood-serving vs. regional-draw commercial uses.
"""
import gzip
import csv
from collections import Counter, defaultdict

RESIDENT_WORKER = "/root/.claude/uploads/6177e088-47b9-5624-b64f-1194a08625f3/3e63bf46-10176838_Arizona-Rio_Grande_resident_worker_report.tsv.gz"
SUMMARY = "/tmp/claude-0/-home-claude-arizona-riogrande/6177e088-47b9-5624-b64f-1194a08625f3/scratchpad/inspect/10176838_Arizona-Rio_Grande_expanded_cel_cdl_detailed_report_summary.tsv.gz"

def read_tsv_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
        yield from csv.DictReader(f, delimiter='\t')

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
