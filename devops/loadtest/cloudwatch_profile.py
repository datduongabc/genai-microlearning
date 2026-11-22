"""Resource profile of a run on AWS, from CloudWatch: CPU and memory of each ECS service, and the
database's CPU, connections and free memory, one-minute points between two UTC times.

    python cloudwatch_profile.py 2026-11-22T10:00:00Z 2026-11-22T10:30:00Z > profile.json
"""

import json
import sys
from datetime import datetime

import boto3

CLUSTER = "microlearning-demo"
SERVICES = ["api", "worker", "renderer", "gateway"]


def series(cw, namespace: str, metric: str, dims: dict, start: datetime, end: datetime, stat: str) -> list:
    points = cw.get_metric_statistics(
        Namespace=namespace, MetricName=metric, StartTime=start, EndTime=end, Period=60, Statistics=[stat],
        Dimensions=[{"Name": k, "Value": v} for k, v in dims.items()])["Datapoints"]
    return [round(p[stat], 1) for p in sorted(points, key=lambda p: p["Timestamp"])]


def summary(values: list) -> dict:
    return {"points": len(values), "mean": round(sum(values) / len(values), 1), "max": max(values)} if values else {}


def main(start_text: str, end_text: str) -> None:
    start, end = (datetime.fromisoformat(t) for t in (start_text, end_text))
    cw = boto3.client("cloudwatch")
    out = {"window": [start_text, end_text], "services": {}, "database": {}}
    for service in SERVICES:
        dims = {"ClusterName": CLUSTER, "ServiceName": service}
        # ECS reports these as a share of what the service reserved, averaged over its tasks.
        out["services"][service] = {
            "cpu_percent": summary(series(cw, "AWS/ECS", "CPUUtilization", dims, start, end, "Average")),
            "cpu_percent_peak_task": summary(series(cw, "AWS/ECS", "CPUUtilization", dims, start, end, "Maximum")),
            "memory_percent": summary(series(cw, "AWS/ECS", "MemoryUtilization", dims, start, end, "Average")),
        }
    db = {"DBInstanceIdentifier": CLUSTER}
    out["database"] = {
        "cpu_percent": summary(series(cw, "AWS/RDS", "CPUUtilization", db, start, end, "Maximum")),
        "connections": summary(series(cw, "AWS/RDS", "DatabaseConnections", db, start, end, "Maximum")),
        "freeable_memory_mb": summary([v / 2**20 for v in series(cw, "AWS/RDS", "FreeableMemory", db, start, end, "Minimum")]),
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
