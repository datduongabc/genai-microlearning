"""Per-level latency of a mixed_read.js run, from its CSV output: only the hold of each stage, not the ramps.

    python analyze_mixed.py mixed.csv
"""

import csv
import sys

# (label, start s, end s) of each hold in mixed_read.js, from the first request.
HOLDS = [("300 users", 60, 180), ("600 users", 240, 360), ("1200 users", 480, 660)]


def pct(values: list[float], p: float) -> float:
    return values[min(len(values) - 1, round(p * (len(values) - 1)))]


def main(path: str) -> None:
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["metric_name"] == "http_req_duration" and r.get("group", "") != "::setup":
                rows.append((float(r["timestamp"]), float(r["metric_value"]), r["status"], r.get("page", "")))
    t0 = min(t for t, *_ in rows)
    print(f"{'level':<11} {'req/s':>6} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'errors':>7}")
    for label, start, end in HOLDS:
        held = [(v, s) for t, v, s, _ in rows if start <= t - t0 < end]
        if not held:
            continue
        times = sorted(v for v, _ in held)
        errors = sum(s != "200" for _, s in held)
        print(f"{label:<11} {len(held) / (end - start):6.1f} {pct(times, .5):7.0f} {pct(times, .95):7.0f} "
              f"{pct(times, .99):7.0f} {errors / len(held):7.2%}")


if __name__ == "__main__":
    main(sys.argv[1])
