"""HTTP integration boundary; provider and tool-loop logic belongs to module D."""

import asyncio
import importlib
import logging

from fastapi import APIRouter, Request

from backend.app.api.dependencies import CurrentStore
from backend.app.contracts import AIResponse, ExplainRequest, InvestigateRequest
from backend.app.errors import APIError
from backend.app.store import ResultStore

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)


def local_reference(body: ExplainRequest, store: ResultStore, reason: str) -> AIResponse:
    """Serve already calculated text while D is absent or unavailable."""
    if body.target.kind == "node":
        node = store.get_node(body.target.id)
        summary = node.role_explanation + "\n" + node.priority_explanation
        limitations = list(node.quality.reasons)
        if node.quality.outbound_censored is True:
            limitations.append("Исходящие связи ограничены глубиной наблюдения; ноль не доказывает отсутствие переводов.")
        elif node.quality.outbound_censored is None:
            limitations.append("Полнота наблюдения исходящих связей неизвестна.")
        if node.quality.inbound_incomplete is not False:
            limitations.append("Входящие переводы наблюдаются неполно либо их полнота неизвестна.")
    else:
        cluster = store.get_cluster(body.target.id)
        summary = cluster.hypothesis.text
        limitations = list(cluster.hypothesis.limitations)
    limitations.append("Локальная справка из текущего расчёта. Дополнительные AI-проверки не выполнялись.")
    return AIResponse(
        run_id=store.run_id, status="fallback", summary=summary, claims=[],
        limitations=list(dict.fromkeys(limitations)), checks=[], fallback_reason=reason,
    )


async def invoke_service(action: str, body: ExplainRequest, request: Request, store: ResultStore) -> AIResponse:
    # Validate before even importing or invoking the optional service/provider.
    if body.run_id != store.run_id:
        raise APIError(409, "stale_run", "Run changed. Refresh the data and submit the request with the current run_id.")
    if body.target.kind == "node":
        store.get_node(body.target.id)
    else:
        store.get_cluster(body.target.id)
    service = request.app.state.ai_service
    if service is None:
        try:
            service = importlib.import_module("backend.app.ai.service")
        except ImportError:
            return local_reference(body, store, "ai_service_unavailable")
        except Exception:
            logger.warning("AI service could not be initialized; serving local reference")
            return local_reference(body, store, "ai_service_error")
    try:
        # Native D owns the single deadline and preserves completed checks on
        # timeout. A competing outer deadline would discard that trace.
        timeout = (
            None if getattr(service, "__name__", None) == "backend.app.ai.service"
            else request.app.state.settings.ai_timeout_seconds
        )
        async with asyncio.timeout(timeout):
            result = await getattr(service, action)(body, store)
            # D may return a mutable model instance; validate its nested values again.
            if isinstance(result, AIResponse):
                result = result.model_dump(mode="python")
            response = AIResponse.model_validate(result)
        if response.run_id != store.run_id:
            return local_reference(body, store, "invalid_ai_run")
        return response
    except TimeoutError:
        return local_reference(body, store, "ai_timeout")
    except Exception as exc:
        # D exports its request error at the public service boundary. Keep the
        # module optional while preserving client errors instead of masking them.
        request_error = getattr(service, "RequestError", None)
        if isinstance(request_error, type) and isinstance(exc, request_error):
            raise APIError(exc.status_code, exc.code, exc.message) from exc
        logger.warning("AI service %s failed; serving local reference", action)
        return local_reference(body, store, "ai_service_error")


@router.post("/ai/explain", response_model=AIResponse)
async def explain(body: ExplainRequest, request: Request, store: CurrentStore) -> AIResponse:
    return await invoke_service("explain", body, request, store)


@router.post("/ai/investigate", response_model=AIResponse)
async def investigate(body: InvestigateRequest, request: Request, store: CurrentStore) -> AIResponse:
    return await invoke_service("investigate", body, request, store)
