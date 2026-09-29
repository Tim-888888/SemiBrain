from semibrain_common.http import create_app
from semibrain_common.runtime import configured

from semibrain_conversation import access, auth, conversations, history, resources
from semibrain_conversation.configuration import router as configuration_router
from semibrain_conversation.exports import router as exports_router
from semibrain_conversation.extensions import router as extensions_router
from semibrain_conversation.graph import router as graph_router
from semibrain_conversation.knowledge_revisions import router as revisions_router
from semibrain_conversation.memory import router as memory_router
from semibrain_conversation.operations import router as operations_router
from semibrain_conversation.storage import initialize
from semibrain_conversation.wiki import router as wiki_router

app = create_app("conversation-service")
app.include_router(operations_router)
app.include_router(wiki_router)
app.include_router(graph_router)
app.include_router(extensions_router)
app.include_router(configuration_router)
app.include_router(revisions_router)
app.include_router(memory_router)
app.include_router(exports_router)
app.include_router(auth.router)
app.include_router(access.router)
app.include_router(history.router)
app.include_router(conversations.router)
app.include_router(resources.router)


@app.on_event("startup")
def startup():
    if configured():
        initialize()
