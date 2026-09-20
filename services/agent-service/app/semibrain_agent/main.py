from semibrain_common.http import create_app
from semibrain_common.runtime import configured

from semibrain_agent.runs import router
from semibrain_agent.storage import initialize

app = create_app("agent-service")
app.include_router(router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
