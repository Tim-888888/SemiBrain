"""Opt-in MongoDBSaver compatibility probe, not the production recovery coordinator."""

import asyncio
import json
import os
from uuid import uuid4

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient


async def probe():
    client = MongoClient(
        os.environ["SEMIBRAIN_CHECKPOINT_PROBE_URI"], serverSelectionTimeoutMS=10000
    )
    client.admin.command("ping")
    saver = MongoDBSaver(client, db_name="foundation_checkpoint_probe")
    thread_id = str(uuid4())
    base = {"configurable": {"thread_id": thread_id, "checkpoint_ns": "agent"}}
    committed = empty_checkpoint()
    committed["channel_values"] = {
        "markdown": "Verified checkpoint",
        "execution_ref": {"run_id": thread_id},
    }
    config = await saver.aput(base, committed, {"source": "update", "step": 1, "parents": {}}, {})
    orphan = empty_checkpoint()
    orphan["channel_values"] = {"markdown": "Uncommitted later checkpoint"}
    await saver.aput(config, orphan, {"source": "update", "step": 2, "parents": {}}, {})
    await saver.aput_writes(config, [("review", "pending")], "task-1")
    restored = await saver.aget_tuple(config)
    assert restored.checkpoint["id"] == committed["id"]
    assert restored.checkpoint["channel_values"]["markdown"] == "Verified checkpoint"
    assert restored.pending_writes
    assert (await saver.aget_tuple(base)).checkpoint["id"] == orphan["id"]

    async def isolated(index):
        cfg = {"configurable": {"thread_id": str(uuid4()), "checkpoint_ns": "agent"}}
        cp = empty_checkpoint()
        cp["channel_values"] = {"owner": index}
        saved = await saver.aput(cfg, cp, {"source": "update", "step": 0, "parents": {}}, {})
        assert (await saver.aget_tuple(saved)).checkpoint["channel_values"]["owner"] == index

    await asyncio.gather(*(isolated(i) for i in range(8)))
    client.close()
    return {
        "driver": "synchronous MongoClient",
        "async_saver": "passed",
        "explicit_committed_checkpoint": "passed",
        "newer_orphan_not_selected": "passed",
        "pending_writes": "passed",
        "parallel_namespaces": 8,
        "production_recovery_acceptance": "not_executed",
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe())))
