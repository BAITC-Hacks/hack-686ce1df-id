"""Explicit local editing capabilities for a synthetic, versioned dataset."""

from fastapi import APIRouter, Request

from backend.app.demo.models import (
    AddNodeRequest, AddTransferRequest, DemoInfoResponse, MutationResponse, NodeName,
)
from backend.app.demo.repository import DemoError, DemoRepository
from backend.app.errors import APIError

router = APIRouter(tags=["demo"])


@router.get("/demo", response_model=DemoInfoResponse)
def demo_info(request: Request) -> DemoInfoResponse:
    snapshot = request.state.snapshot
    if snapshot is None:
        return DemoInfoResponse(run_id=None, enabled=False)
    store, source = snapshot.store, snapshot.source
    if source is None or request.app.state.demo_repository is None:
        return DemoInfoResponse(run_id=store.run_id, enabled=False, node_count=len(store.nodes_by_gid))
    return DemoInfoResponse(
        run_id=store.run_id, enabled=True,
        node_count=len(source.nodes), transfer_count=len(source.transfers),
        nodes=tuple(NodeName(gid=node.gid, display_name=node.display_name) for node in source.nodes),
        groups=source.groups,
    )


def editable_repository(request: Request) -> DemoRepository:
    if not request.app.state.settings.demo_mode:
        raise APIError(403, "demo_read_only", "Adding records is available only in editable demo mode.")
    repository = request.app.state.demo_repository
    if repository is None:
        raise APIError(503, "data_not_ready", "Editable demo data could not be loaded. Check the server log.")
    return repository


def mutate(request: Request, body: AddNodeRequest | AddTransferRequest, kind: str) -> MutationResponse:
    repository = editable_repository(request)
    try:
        result = repository.add_node(body) if kind == "node" else repository.add_transfer(body)
    except DemoError as exc:
        # A competing request may have published since this request was pinned.
        # The repository chooses the precise run under its mutation lock.
        request.state.response_run_id = exc.run_id
        raise APIError(exc.status_code, exc.code, exc.message) from exc
    request.state.response_run_id = result.response.run_id
    # Preserve the existing testing/service compatibility alias. Requests always
    # pin repository.snapshot, including when this is an older replay receipt.
    request.app.state.store = repository.snapshot.store
    return result.response


@router.post("/demo/nodes", response_model=MutationResponse)
def add_node(body: AddNodeRequest, request: Request) -> MutationResponse:
    return mutate(request, body, "node")


@router.post("/demo/transfers", response_model=MutationResponse)
def add_transfer(body: AddTransferRequest, request: Request) -> MutationResponse:
    return mutate(request, body, "transfer")
