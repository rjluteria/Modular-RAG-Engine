import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.body_limit import RequestBodyLimit
from api.routes import configuration, ingest, jobs, query
from core.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("modular_rag")

app = FastAPI(title="Modular RAG Engine API", version="1.0.0")

app.add_middleware(RequestBodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def dependency_error(request, exc):
    from fastapi.responses import JSONResponse

    logger.error("Request failed (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"error": {"code": "service_unavailable", "message": "Service unavailable; retry later"}},
    )


app.include_router(configuration.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    return {"data": {"status": "ok"}}


@app.get("/api/v1/health/ready")
def health_ready():
    from fastapi.responses import JSONResponse
    from redis import Redis

    from core.registry import check_vector_store_ready

    checks = {"vector_store": "unavailable", "queue": "unavailable"}
    try:
        check_vector_store_ready()
        checks["vector_store"] = "ok"
    except Exception:  # noqa: BLE001 -- return sanitized dependency status
        logger.warning("Dependency readiness check failed")
    try:
        if settings.shared_state_provider == "azure_blob":
            from core.adapters.state.blob_state import blob_state
            with blob_state() as state:
                state.container.get_container_properties()
        else:
            with Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3) as connection:
                connection.ping()
        checks["queue"] = "ok"
    except Exception:  # noqa: BLE001 -- return sanitized dependency status
        logger.warning("Dependency readiness check failed")
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"data": {"status": "ok" if healthy else "degraded", "checks": checks}},
    )


# Application Insights (prod only) — no-op locally when the connection
# string isn't set, so this file never branches on environment.
if settings.applicationinsights_connection_string:
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)
