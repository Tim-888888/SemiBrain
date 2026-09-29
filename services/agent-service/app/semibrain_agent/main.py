from semibrain_common.http import create_app
from semibrain_common.operations import router_for
from semibrain_common.runtime import configured

from semibrain_agent.configuration import router as configuration_router
from semibrain_agent.control import router as control_router
from semibrain_agent.diagnostics import router as diagnostics_router
from semibrain_agent.evaluations import router as evaluations_router
from semibrain_agent.memory import router as memory_router
from semibrain_agent.retention import router as retention_router
from semibrain_agent.runs import router
from semibrain_agent.storage import initialize

app = create_app("agent-service")
app.include_router(router_for("agent"))
app.include_router(router)
app.include_router(control_router)
app.include_router(configuration_router)
app.include_router(diagnostics_router)
app.include_router(evaluations_router)
app.include_router(memory_router)
app.include_router(retention_router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
