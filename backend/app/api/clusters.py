from fastapi import APIRouter

from backend.app.api.dependencies import CurrentStore
from backend.app.contracts import ClusterResponse, ClustersResponse, Identifier

router = APIRouter(tags=["clusters"])


@router.get("/clusters", response_model=ClustersResponse)
def get_clusters(store: CurrentStore) -> ClustersResponse:
    return ClustersResponse(run_id=store.run_id, items=store.list_clusters())


@router.get("/clusters/{cluster_id:path}", response_model=ClusterResponse)
def get_cluster(cluster_id: Identifier, store: CurrentStore) -> ClusterResponse:
    return ClusterResponse(run_id=store.run_id, cluster=store.get_cluster(cluster_id))
