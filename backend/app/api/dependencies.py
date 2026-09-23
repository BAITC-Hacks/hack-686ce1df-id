from typing import Annotated

from fastapi import Depends, Request

from backend.app.errors import APIError
from backend.app.store import ResultStore


def get_store(request: Request) -> ResultStore:
    snapshot = request.state.snapshot
    store = snapshot.store if snapshot is not None else None
    if store is None:
        raise APIError(503, "data_not_ready", "No complete run is loaded. Configure AML_RUN_DIR and restart the server.")
    return store


CurrentStore = Annotated[ResultStore, Depends(get_store)]
