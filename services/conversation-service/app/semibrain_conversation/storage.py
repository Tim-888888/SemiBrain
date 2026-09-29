from semibrain_common.runtime import migrate

from semibrain_conversation.auth import db


def initialize():
    migrate(db(), "answer-export-001", [
        ("gateway_runs", [("answer_export.status", 1), ("answer_export.next_at", 1)], {}),
    ])
    migrate(db(), "conversation-context-001", [
        ("conversation_contexts", [("owner_id", 1), ("conversation_id", 1), ("covered_revision", -1), ("created_at", -1)], {}),
    ])
    migrate(
        db(),
        "b-001",
        [
            ("users", [("username_key", 1)], {"unique": True}),
            ("conversations", [("request_key", 1)], {"unique": True}),
            ("conversations", [("owner_id", 1), ("created_at", -1), ("_id", -1)], {}),
            ("gateway_runs", [("request_key", 1)], {"unique": True}),
            (
                "messages",
                [("conversation_id", 1), ("input_revision", 1), ("position", 1)],
                {"unique": True},
            ),
            ("gateway_events", [("aggregate_id", 1), ("sequence", 1)], {"unique": True}),
        ],
    )
