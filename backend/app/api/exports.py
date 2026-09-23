from urllib.parse import quote

from fastapi import APIRouter, Response

from backend.app.api.dependencies import CurrentStore

router = APIRouter(tags=["exports"])


@router.get("/exports/{name}", response_class=Response, responses={200: {"description": "Immutable file from the current manifest.files allowlist."}})
def get_export(name: str, store: CurrentStore) -> Response:
    artifact = store.get_export(name)
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.filename, safe="")},
    )
