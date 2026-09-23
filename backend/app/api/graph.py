from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.dependencies import CurrentStore
from backend.app.contracts import GraphResponse
from backend.app.errors import APIError

router = APIRouter(tags=["graph"])


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    store: CurrentStore,
    gid: Annotated[str | None, Query(min_length=1)] = None,
    cluster_id: Annotated[str | None, Query(min_length=1)] = None,
    component_id: Annotated[str | None, Query(min_length=1)] = None,
    radius: Annotated[int, Query(ge=1, le=2)] = 1,
    limit: Annotated[int, Query(ge=1, le=300)] = 300,
) -> GraphResponse:
    if sum(value is not None for value in (gid, cluster_id, component_id)) != 1:
        raise APIError(422, "invalid_graph_selector", "Specify exactly one of gid, cluster_id, component_id.")
    return store.get_graph(gid=gid, cluster_id=cluster_id, component_id=component_id, radius=radius, limit=limit)
