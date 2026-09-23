from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from backend.app.contracts import ErrorDetail, ErrorResponse


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    snapshot = getattr(request.state, "snapshot", None)
    run_id = getattr(request.state, "response_run_id", None)
    if run_id is None and snapshot is not None:
        run_id = snapshot.store.run_id
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message),
        run_id=run_id,
    )
    return JSONResponse(status_code=status, content=body.model_dump())


async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.code, exc.message)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not echo request bodies, keys, arbitrary questions, or raw invalid values.
    locations = sorted({".".join(map(str, item["loc"])) for item in exc.errors()})
    return error_response(request, 422, "invalid_request", "Invalid request fields: " + ", ".join(locations))


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    return error_response(request, exc.status_code, code, str(exc.detail))
