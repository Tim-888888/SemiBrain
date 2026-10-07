"""Title latency cannot hold up chat commands, SSE projection, or exports."""

from semibrain_common.worker import PollTask, worker_app

from semibrain_conversation.storage import initialize
from semibrain_conversation.titles import process_one

app = worker_app("conversation")
QUEUE = "celery_conversation_titles_v1"
_initialized = False
app.conf.task_default_queue = QUEUE
app.conf.task_routes = {"titles.*": {"queue": QUEUE}}
app.conf.beat_schedule = {"titles-tick": {"task": "titles.tick", "schedule": 3.0}}


@app.task(name="titles.tick", base=PollTask)
def tick():
    global _initialized
    if not _initialized:
        initialize()
        _initialized = True
    if process_one():
        tick.delay()
