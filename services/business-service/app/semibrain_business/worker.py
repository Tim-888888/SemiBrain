from semibrain_common.runtime import relay
from semibrain_common.worker import worker_app

from semibrain_business.knowledge import process_one
from semibrain_business.security import db
from semibrain_business.storage import initialize
from semibrain_business.tools import execute_one

app = worker_app("business")


@app.task(name="business.tick")
def tick():
    initialize()
    relay(db())
    query.delay()
    ingest.delay()


@app.task(name="business.query")
def query():
    execute_one()


@app.task(name="business.ingest")
def ingest():
    process_one()
