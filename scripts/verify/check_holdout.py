"""Audit sealed fixtures without loading or displaying private oracle contents."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
root = args.directory.resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def check(relative, expected):
    path = (root / relative).resolve()
    assert path.is_relative_to(root)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


check("dataset.json", manifest["dataset_sha256"])
assert len(manifest["tasks"]) >= 120
assert len({t["task_id"] for t in manifest["tasks"]}) == len(manifest["tasks"])
for task in manifest["tasks"]:
    check(task["input_path"], task["input_sha256"])
    check(task["oracle_path"], task["oracle_sha256"])
for item in manifest["formats"]:
    check("formats/" + item["path"], item["sha256"])
counts = Counter(item["format"] for item in manifest["formats"])
assert len(counts) == 12 and min(counts.values()) >= 3
data = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
assert all(not row["lot_id"].startswith("DEV") for row in data["tables"]["lots"])
assert all(row["event_time"] < "2026-01-01" for row in data["tables"]["test_results"])
print(
    json.dumps(
        {
            "integrity": "passed",
            "task_count": len(manifest["tasks"]),
            "format_counts": dict(counts),
            "leakage_boundary": "offline_only",
            "product_evaluation": "not_executed",
        }
    )
)
