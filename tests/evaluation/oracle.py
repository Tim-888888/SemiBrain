"""Offline reference calculator. Never imported by a running application service."""

from collections import defaultdict
from datetime import datetime


def yield_oracle(records, query):
    def as_time(value):
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    groups = defaultdict(list)
    for row in records:
        if (
            row["lot_id"] not in query.lot_ids
            or row["stage"] != query.stage
            or row["program_version"] != query.program_version
            or not row["is_valid"]
            or as_time(row["ingested_at"]) > query.as_of
            or as_time(row["event_time"]) > query.as_of
        ):
            continue
        key = tuple(
            row[k] for k in ["source_system", "lot_id", "unit_id", "stage", "program_version"]
        )
        groups[key].append(row)
    values = []
    for rows in groups.values():
        ordered = sorted(
            rows, key=lambda r: (r["attempt_no"], as_time(r["event_time"]), r["test_result_id"])
        )
        first = ordered[0]
        if not query.start <= as_time(first["event_time"]) < query.end:
            continue
        values.append((ordered[0] if query.metric == "first" else ordered[-1])["pass_flag"])
    n, d = sum(values), len(values)
    return {"numerator": n, "denominator": d, "value": n / d if d else None}
