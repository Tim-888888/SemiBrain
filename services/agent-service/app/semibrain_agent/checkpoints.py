"""Explicit committed checkpoints around graph node boundaries; never select latest."""

import asyncio

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.mongodb import MongoDBSaver
from semibrain_common.runtime import mongo, now, transaction
from semibrain_contracts.models import assert_no_credentials

from semibrain_agent.harness import RunStopped

GRAPH_VERSION = "single-investigator-v2"
STATE_VERSION = "1.0"


class Checkpoints:
    def __init__(self, harness, task_id, attempt, *, graph_version=GRAPH_VERSION,
                 state_version=STATE_VERSION):
        self.harness = harness
        self.graph_version, self.state_version = graph_version, state_version
        self.saver = MongoDBSaver(mongo(), db_name="agent_db")
        self.namespace = f"{task_id}/attempt-{attempt}"

    def restore(self):
        run = self.harness.check()
        pointer = run.get("committed_checkpoint_ref")
        if not pointer:
            return {
                "phase": "understand",
                "step": 0,
                "round": 0,
                "evidence_ids": [],
                "call_fingerprints": [],
                "review_count": 0,
            }
        saved = asyncio.run(self.saver.aget_tuple(pointer))
        if not saved:
            raise RuntimeError("COMMITTED_CHECKPOINT_MISSING")
        values = saved.checkpoint["channel_values"]
        if (values["graph_version"] != self.graph_version
                or values["state_version"] != self.state_version):
            raise RuntimeError("CHECKPOINT_VERSION_INCOMPATIBLE")
        return values["state"]

    def save(self, state):
        assert_no_credentials(state)
        current = self.harness.check()
        generation = current.get("checkpoint_generation", 0)
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {
            "graph_version": self.graph_version,
            "state_version": self.state_version,
            "state": state,
        }
        config = {
            "configurable": {"thread_id": self.harness.run_id, "checkpoint_ns": self.namespace}
        }
        # Save through the supported public async API. The connection remains process-owned.
        stored = asyncio.run(
            self.saver.aput(
                config, checkpoint, {"source": "update", "step": state["step"], "parents": {}}, {}
            )
        )

        def commit(session):
            changed = self.harness.db.runs.update_one(
                {**self.harness.predicate(), "checkpoint_generation": generation}
                if generation
                else {
                    **self.harness.predicate(),
                    "$or": [
                        {"checkpoint_generation": 0},
                        {"checkpoint_generation": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "committed_checkpoint_ref": stored,
                        "checkpoint_generation": generation + 1,
                        "checkpoint_committed_at": now(),
                        "graph_phase": state["phase"],
                    }
                },
                session=session,
            )
            if not changed.modified_count:
                # This isolated orphan is harmless: restore only reads the committed pointer.
                raise RunStopped("STALE_CHECKPOINT_COMMIT")

        transaction(commit)
        return stored
