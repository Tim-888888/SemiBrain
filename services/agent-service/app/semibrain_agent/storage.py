from semibrain_common.runtime import migrate

from semibrain_agent.runs import db


def initialize():
    migrate(db(), "d-retention-002", [("evidence", [("lineage_refs", 1)], {})])
    migrate(db(), "d-efficiency-001", [
        ("investigation_progress", [("run_id", 1), ("created_at", 1)], {}),
        ("readonly_requests", [("run_id", 1), ("status", 1)], {}),
    ])
    migrate(
        db(),
        "c-001",
        [
            ("runs", [("status", 1), ("lease_until", 1)], {}),
            ("evidence", [("run_id", 1)], {}),
            ("reports", [("run_id", 1), ("revision", 1)], {"unique": True}),
            ("model_calls", [("run_id", 1), ("created_at", 1)], {}),
            ("model_turns", [("run_id", 1)], {}),
            ("observations", [("run_id", 1)], {}),
            ("tool_calls", [("run_id", 1)], {}),
            (
                "evidence",
                [("run_id", 1), ("marker", 1)],
                {"unique": True, "partialFilterExpression": {"marker": {"$exists": True}}},
            ),
        ],
    )
