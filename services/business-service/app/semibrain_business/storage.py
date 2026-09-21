from semibrain_common.runtime import migrate

from semibrain_business.security import db


def initialize():
    migrate(
        db(),
        "c-001",
        [
            ("ingestion_jobs", [("request_key", 1)], {"unique": True}),
            ("ingestion_jobs", [("status", 1), ("lease_until", 1)], {}),
            ("documents", [("owner_id", 1), ("visibility", 1), ("active_version", 1)], {}),
            ("chunks", [("document_id", 1), ("version", 1)], {}),
            ("tool_jobs", [("status", 1), ("lease_until", 1)], {}),
            ("assets", [("document_id", 1)], {}),
            ("web_snapshots", [("owner_id", 1), ("run_id", 1)], {}),
            ("web_attempts", [("run_id", 1)], {}),
        ],
    )
