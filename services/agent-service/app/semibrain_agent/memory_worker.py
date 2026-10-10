"""Separate queue: extraction latency never occupies an answer worker slot."""

from semibrain_common.worker import PollTask, worker_app

from semibrain_agent.memory_extraction import process_one
from semibrain_agent.storage import initialize

app = worker_app("agent")
QUEUE = "celery_agent_memory_v1"
app.conf.task_default_queue = QUEUE
app.conf.task_routes = {"memory.*": {"queue": QUEUE}}
app.conf.beat_schedule = {"memory-tick": {"task": "memory.tick", "schedule": 10.0}}
_initialized = False


@app.task(name="memory.tick", base=PollTask)
def tick():
    global _initialized
    if not _initialized:
        initialize()
        _initialized = True
    if process_one():
        tick.delay()
