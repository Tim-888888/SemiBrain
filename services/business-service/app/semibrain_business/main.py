from semibrain_common.http import create_app

from semibrain_business.foundation_api import router

app = create_app("business-service")
app.include_router(router)
