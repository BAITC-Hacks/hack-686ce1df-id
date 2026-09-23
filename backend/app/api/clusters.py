from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.dependencies import CurrentStore
from backend.app.contracts import ClusterResponse, ClustersResponse, Identifier

router = APIRouter(tags=["clusters"])


@router.get("/clusters", response_model=ClustersResponse)
def get_clusters(store: CurrentStore) -> ClustersResponse:
    return ClustersResponse(run_id=store.run_id, items=store.list_clusters())


@router.get("/clusters/{cluster_id:path}", response_model=ClusterResponse)
def get_cluster(cluster_id: Identifier, store: CurrentStore) -> ClusterResponse:
    return ClusterResponse(run_id=store.run_id, cluster=store.get_cluster(cluster_id))


@router.get("/cluster", response_model=ClusterResponse)
def lookup_cluster(
    cluster_id: Annotated[str, Query(min_length=1)], store: CurrentStore,
) -> ClusterResponse:
    """Keep slashes and dot segments inside the exact identifier."""
    return get_cluster(cluster_id, store)
