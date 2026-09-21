from semibrain_common.runtime import consume, relay
from semibrain_common.worker import PollTask, worker_app

from semibrain_agent.control import reconcile_cancellations
from semibrain_agent.runs import accept, db, execute_one
from semibrain_agent.storage import initialize

app = worker_app("agent")


def command(event, session):
    if event["event_type"] != "run.requested":
        raise ValueError("UNKNOWN_RUN_COMMAND")
    accept(event["payload"], session)


@app.task(name="agent.tick", base=PollTask)
def tick():
    initialize()
    consume(db(), "stream:runs", "agent-run-requests", command)
    relay(db())
    reconcile_cancellations()
    work.delay()


@app.task(name="agent.work", base=PollTask)
def work():
    execute_one()
