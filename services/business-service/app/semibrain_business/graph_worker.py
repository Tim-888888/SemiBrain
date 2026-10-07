"""Dedicated low-concurrency graph queue, independent of ingest and chat workers."""

import logging

from semibrain_common.worker import PollTask, worker_app

from semibrain_business import graph, graph_extraction
from semibrain_business.storage import initialize

app = worker_app("business")
QUEUE = "celery_business_graph_v1"
app.conf.task_default_queue = QUEUE
app.conf.task_routes = {"graph.*": {"queue": QUEUE}}
app.conf.beat_schedule = {"graph-tick": {"task": "graph.tick", "schedule": 10.0}}


@app.task(name="graph.tick", base=PollTask)
def tick():
    initialize()
    try:
        graph_extraction.scan_published()
    except Exception:
        logging.getLogger(__name__).warning("Graph source discovery unavailable; retry next tick")
    work.delay()


@app.task(name="graph.work", base=PollTask)
def work():
    progressed = graph_extraction.process_one()
    current = graph.state()
    if graph.projection.configured() and current.get("projected_revision") != current["revision"]:
        try:
            graph.rebuild_projection()
        except Exception:
            logging.getLogger(__name__).warning("Graph projection pending; retrieval uses authorized text fallback")
    if progressed:
        work.delay()
