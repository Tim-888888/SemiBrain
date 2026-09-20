from semibrain_common.http import create_app
from semibrain_common.runtime import configured

from semibrain_business import knowledge_api, tools
from semibrain_business.foundation_api import router
from semibrain_business.storage import initialize

app = create_app("business-service")
app.include_router(router)
app.include_router(tools.router)
app.include_router(knowledge_api.router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
