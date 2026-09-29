from semibrain_common.runtime import migrate

from semibrain_business.security import db


def initialize():
    migrate(db(), "e-graph-001", [
        ("graph_edges", [("document_id", 1), ("version", 1)], {}),
        ("graph_commands", [("at", -1)], {}),
    ])
    migrate(db(), "e-wiki-001", [
        ("wiki_revisions", [("document_id", 1), ("created_at", -1)], {}),
        ("wiki_revisions", [("source_refs.document_id", 1), ("source_refs.version", 1)], {}),
    ])
    migrate(db(), "e-search-001", [
        ("search_audits", [("created_at", -1)], {}),
        ("search_config_history", [("revision", 1)], {"unique": True}),
    ])
    migrate(db(), "knowledge-context-001", [
        ("knowledge_parents", [("document_id", 1), ("version", 1)], {}),
        ("ingestion_jobs", [("document_id", 1), ("created_at", -1)], {}),
    ])
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
