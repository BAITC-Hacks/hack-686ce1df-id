import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from backend.app.api import ai, clusters, exports, graph, nodes
from backend.app.config import FIXTURE_DIR, Settings
from backend.app.contracts import ErrorResponse, HealthResponse
from backend.app.errors import (
    APIError, error_response, handle_api_error, handle_http_error, handle_validation_error,
)
from backend.app.store import RecordNotFoundError, ResultStore, StoreLoadError

logger = logging.getLogger(__name__)
ERROR_RESPONSES = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        404: "Identifier or export not found", 409: "Stale AI run_id",
        422: "Invalid request", 503: "No completed run available",
    }.items()
}


class FrontendFiles(StaticFiles):
    """Serve a Vite build and extensionless client routes with safe path lookup."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


def create_app(settings: Settings | None = None, *, store: ResultStore | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            directory = FIXTURE_DIR if settings.fixture_mode else settings.run_dir
            if directory is not None:
                try:
                    candidate = ResultStore.from_directory(directory)
                    if not settings.fixture_mode and (
                        directory.resolve() == FIXTURE_DIR.resolve()
                        or candidate.run_id.startswith("fixture-")
                    ):
                        raise StoreLoadError("Synthetic fixtures require AML_FIXTURE_MODE=true or --fixtures.")
                    app.state.store = candidate
                except StoreLoadError as exc:
                    app.state.load_error = str(exc)
                    logger.error("Result run is not ready: %s", exc)
            else:
                app.state.load_error = "AML_RUN_DIR is not configured."
        if settings.fixture_mode:
            logger.warning("FIXTURE MODE: all displayed data is artificial and is not an analytics result.")
        yield

    app = FastAPI(
        title="Money Graph API", version="1.0", lifespan=lifespan,
        description="One completed run. Synthetic data requires explicit fixture mode; see X-Data-Source.",
        responses=ERROR_RESPONSES,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.load_error = None
    app.state.ai_service = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Run-Id", "X-Contract-Version", "X-Data-Source", "Content-Disposition"],
    )

    @app.middleware("http")
    async def run_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            current = app.state.store
            response.headers["X-Contract-Version"] = "1.0"
            response.headers["X-Data-Source"] = (
                "unavailable" if current is None else "fixtures" if settings.fixture_mode else "artifacts"
            )
            response.headers["Cache-Control"] = "no-store"
            if current is not None:
                response.headers["X-Run-Id"] = quote(current.run_id, safe="")
        return response

    app.add_exception_handler(APIError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)

    @app.exception_handler(RecordNotFoundError)
    async def missing_record(request: Request, exc: RecordNotFoundError):
        return error_response(request, 404, "not_found", str(exc))

    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        current = app.state.store
        return HealthResponse(run_id=current.run_id if current is not None else None, data_ready=current is not None)

    for router in (nodes.router, clusters.router, graph.router, exports.router, ai.router):
        app.include_router(router, prefix="/api")

    # Unknown API requests must never fall through to the SPA HTML response.
    @app.api_route("/api", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"], include_in_schema=False)
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"], include_in_schema=False)
    def unknown_api(path: str = ""):
        raise APIError(404, "not_found", "Unknown API route.")

    if settings.frontend_dist is not None and (settings.frontend_dist / "index.html").is_file():
        app.mount("/", FrontendFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return app


app = create_app()
