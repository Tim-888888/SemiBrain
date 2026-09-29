from semibrain_common.http import create_app
from semibrain_common.operations import router_for
from semibrain_common.runtime import configured

from semibrain_business import (
    graph,
    knowledge_api,
    knowledge_revisions,
    search_governance,
    tools,
    wiki,
)
from semibrain_business.foundation_api import router
from semibrain_business.storage import initialize

app = create_app("business-service")
app.include_router(router_for("business"))
app.include_router(router)
app.include_router(tools.router)
app.include_router(knowledge_api.router)
app.include_router(knowledge_revisions.router)
app.include_router(search_governance.router)
app.include_router(wiki.router)
app.include_router(graph.router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
