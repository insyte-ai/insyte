"""Analysis endpoints — fetch results, stream progress (SSE), retry, cancel."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from insyte.semantic.proposals import apply_metric_proposal
from insyte.services.project_service import ProjectServices
from insyte.studio.context import ChatContext
from insyte.studio.dependencies import get_analysis_factory, get_pending, get_services
from insyte.studio.events import AnalysisFactory, sse, stream_analysis
from insyte.studio.schemas import AnalysisResult

router = APIRouter()


@router.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    services: ProjectServices = Depends(get_services),
    pending: dict = Depends(get_pending),
) -> dict:
    stored = services.conversations.get_analysis(analysis_id)
    if stored is not None:
        return json.loads(stored)
    if analysis_id in pending:
        return {"analysis_id": analysis_id, "status": "pending"}
    raise HTTPException(status_code=404, detail="Analysis not found.")


@router.get("/analyses/{analysis_id}/events")
def analysis_events(
    analysis_id: str,
    request: Request,
    services: ProjectServices = Depends(get_services),
    pending: dict = Depends(get_pending),
    analysis_factory: AnalysisFactory = Depends(get_analysis_factory),
) -> StreamingResponse:
    job = pending.pop(analysis_id, None)
    if job is None:
        stored = services.conversations.get_analysis(analysis_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Analysis not found.")
        return StreamingResponse(
            iter([sse("response_completed", {"result": json.loads(stored)})]),
            media_type="text/event-stream",
        )

    layer = services.metrics.layer()
    conversation_id = job["conversation_id"]

    # Prior turns (excluding the just-added current question) give the resolver context for
    # follow-ups like "in that period" or "and by city".
    history: list[tuple[str, str]] = []
    chat_context = None
    if conversation_id:
        prior = services.conversations.messages(conversation_id)
        history = [(m.role, m.content) for m in prior[:-1]][-6:]
        chat_context = services.conversations.latest_context(conversation_id)

    def on_complete(result: AnalysisResult, context: ChatContext | None) -> None:
        result_payload = result.model_dump(mode="json")
        services.conversations.save_analysis(
            analysis_id,
            job["question"],
            result.summary,
            json.dumps(result_payload),
            conversation_id,
        )
        if result.investigation is not None and result.status == "completed":
            services.conversations.save_investigation(
                analysis_id=analysis_id,
                question=job["question"],
                summary=result.investigation.summary or result.summary,
                result_json=result_payload,
                conversation_id=conversation_id,
            )
        services.conversations.add_message(
            conversation_id, "assistant", result.summary, analysis_id
        )
        if context is not None:
            services.conversations.save_context(conversation_id, context, analysis_id)

    def on_proposal(proposal) -> None:  # noqa: ANN001 - callback type lives in semantic module
        current = services.semantic.load()
        if proposal.name not in current.metrics:
            services.semantic.save(apply_metric_proposal(proposal, current))

    stream = stream_analysis(
        analysis_id=analysis_id,
        question=job["question"],
        layer=layer,
        config=services.config,
        schema=services.schema,
        analysis_factory=analysis_factory,
        on_complete=on_complete,
        history=history,
        chat_context=chat_context,
        detailed=bool(job.get("detailed", False)),
        on_proposal=on_proposal,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/analyses/{analysis_id}/retry")
def retry_analysis(
    analysis_id: str,
    services: ProjectServices = Depends(get_services),
    pending: dict = Depends(get_pending),
) -> dict:
    request = services.conversations.get_analysis_request(analysis_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    question, conversation_id = request
    pending[analysis_id] = {"question": question, "conversation_id": conversation_id}
    return {"analysis_id": analysis_id, "stream_url": f"/api/analyses/{analysis_id}/events"}


@router.post("/analyses/{analysis_id}/cancel")
def cancel_analysis(analysis_id: str, pending: dict = Depends(get_pending)) -> dict:
    existed = pending.pop(analysis_id, None) is not None
    return {"analysis_id": analysis_id, "cancelled": existed}
