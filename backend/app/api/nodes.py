from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.dependencies import CurrentStore
from backend.app.contracts import NodeResponse, PrioritiesResponse

router = APIRouter(tags=["nodes"])


@router.get("/nodes/{gid:path}", response_model=NodeResponse)
def get_node(gid: str, store: CurrentStore) -> NodeResponse:
    return NodeResponse(run_id=store.run_id, node=store.get_node(gid))


@router.get("/node", response_model=NodeResponse)
def lookup_node(gid: Annotated[str, Query(min_length=1)], store: CurrentStore) -> NodeResponse:
    """Look up an opaque ID without browser normalization of URL path segments."""
    return get_node(gid, store)


@router.get("/priorities", response_model=PrioritiesResponse)
def get_priorities(
    store: CurrentStore,
    limit: Annotated[int, Query(ge=1)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PrioritiesResponse:
    return PrioritiesResponse(
        run_id=store.run_id,
        items=store.list_priorities(limit=limit, offset=offset),
        total=len(store.nodes_by_gid),
    )
