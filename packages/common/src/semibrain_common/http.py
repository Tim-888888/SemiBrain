from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def create_app(service: str) -> FastAPI:
    app = FastAPI(title=f"SemiBrain {service}", version="0.1.0")

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        try:
            value = str(UUID(supplied))
        except ValueError:
            value = str(uuid4())
        request.state.request_id = value
        response = await call_next(request)
        response.headers["X-Request-ID"] = value
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo request values, passwords, or provider credentials.
        return JSONResponse(
            status_code=422,
            content={
                "schema_version": "1.0",
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": "Request validation failed",
                    "retryable": False,
                    "trace_id": request.state.request_id,
                    "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
                },
            },
        )

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": service, "version": "0.1.0", "stage": "foundation"}

    return app
