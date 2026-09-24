from semibrain_common.runtime import migrate

from semibrain_business.security import db


def initialize():
    migrate(db(), "knowledge-republication-001", [
        ("publication_commands", [("result.document_id", 1), ("result.revision", -1)], {}),
    ])
    migrate(db(), "d-retention-002", [
        ("tool_jobs", [("subject_id", 1), ("result.data.lineage_refs", 1)], {}),
        ("assets", [("owner_id", 1), ("source_refs", 1)], {}),
    ])
    migrate(db(), "d-retention-001", [
        ("retention_objects", [("state", 1), ("checked_at", 1)], {}),
        ("retention_audits", [("audit_expires_at", 1)], {"expireAfterSeconds": 0}),
    ])
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
