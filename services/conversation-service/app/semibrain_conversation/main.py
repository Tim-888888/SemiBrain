from semibrain_common.http import create_app
from semibrain_common.runtime import configured

from semibrain_conversation import access, auth, conversations, history, resources
from semibrain_conversation.knowledge_revisions import router as revisions_router
from semibrain_conversation.operations import router as operations_router
from semibrain_conversation.storage import initialize

app = create_app("conversation-service")
app.include_router(operations_router)
app.include_router(revisions_router)
app.include_router(auth.router)
app.include_router(access.router)
app.include_router(history.router)
app.include_router(conversations.router)
app.include_router(resources.router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
