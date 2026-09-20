from semibrain_common.runtime import migrate

from semibrain_agent.runs import db


def initialize():
    migrate(
        db(),
        "b-001",
        [
            ("runs", [("status", 1), ("lease_until", 1)], {}),
            ("evidence", [("run_id", 1)], {}),
            ("reports", [("run_id", 1), ("revision", 1)], {"unique": True}),
        ],
    )
