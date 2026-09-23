from semibrain_common.http import create_app
from semibrain_common.runtime import configured

from semibrain_agent.control import router as control_router
from semibrain_agent.retention import router as retention_router
from semibrain_agent.runs import router
from semibrain_agent.storage import initialize

app = create_app("agent-service")
app.include_router(router)
app.include_router(control_router)
app.include_router(retention_router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
