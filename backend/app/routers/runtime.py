"""AI-only public routes. Existing session authentication remains authoritative."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import ValidationError
from shopping_ai.analysis import (
    AnalysisReply,
    AnalysisRequest,
    analyze,
    selected_fact_reply,
)
from shopping_ai.browser import BrowserTarget
from shopping_ai.models import AIRequest, Identifier
from shopping_ai.navigation import (
    BrowserGoalPlan,
    BrowserPlanRequest,
    browser_goal_is_unsafe,
    plan_browser_goal,
    validate_browser_plan,
)
from shopping_ai.voice import AudioClip
from sqlalchemy.orm import Session

from app.ai.runtime import inference_fallback
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.repositories.usage_repo import UsageRepository
from app.schemas.common import ErrorResponse
from app.schemas.runtime import (
    Capabilities,
    ClaimedWork,
    ClaimWork,
    Confirmation,
    PublicReply,
    PublicWork,
    Receipt,
    RuntimeEvent,
    SpeechRequest,
    StartConversation,
)
from app.services.runtime_service import reject

router = APIRouter(
    prefix="/v1/ai",
    tags=["AI runtime"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 429, 503)
    },
)


def service(request: Request):
    return request.app.state.runtime_service


@router.post("/navigation/plan", response_model=BrowserGoalPlan)
async def navigation_plan(
    data: BrowserPlanRequest,
    current: User = Depends(get_current_user),
    svc=Depends(service),
    db: Session = Depends(get_db),
):
    """Separate navigation planner; no DOM, execution, mutations or model prose."""
    from app.repositories.runtime_repo import utcnow
    result = plan_browser_goal(data)
    if (
        result.status != "needs_input"
        or svc.settings.AI_RUNTIME_PROVIDER == "mock"
        or browser_goal_is_unsafe(data.message)
    ):
        return result
    count = UsageRepository().increment_and_get_count(db, current.id, utcnow().date(), "ai_daily")
    if count > svc.settings.MAX_AI_CALLS_PER_USER_PER_DAY:
        db.rollback()
        reject("RATE_LIMITED", 429)
    db.commit()  # Release the quota transaction before waiting for the planner.
    trace = inference_fallback.set(None)
    try:
        provider = svc.runtime.conversation.orchestrator.ai.provider
        candidate = await provider.plan_navigation(data)
        return validate_browser_plan(data, candidate, processing="model").model_copy(
            update={"inference_fallback": inference_fallback.get()}
        )
    except Exception:
        return result.model_copy(update={"inference_fallback": inference_fallback.get() or "deterministic"})
    finally:
        inference_fallback.reset(trace)


@router.post("/analysis", response_model=AnalysisReply)
async def shopping_analysis(
    data: AnalysisRequest,
    current: User = Depends(get_current_user),
    svc=Depends(service),
    db: Session = Depends(get_db),
):
    """Extract first; optional model may select fact IDs, never prose or actions."""
    from urllib.parse import urlsplit

    from app.repositories.runtime_repo import utcnow

    for item in data.products:
        url = urlsplit(item.source_url)
        svc.origin(f"{url.scheme}://{url.netloc}", required=True)
    result = analyze(data, now=utcnow(), max_age_seconds=svc.settings.AI_MAX_SNAPSHOT_AGE_SECONDS)
    if result.status != "no_evidence" or svc.settings.AI_RUNTIME_PROVIDER == "mock":
        return result
    if re.search(
        r"\b(reviews?|complaints?|heating|issues?|problems?|shikayat|compare|comparison|"
        r"recommend|suggest|best|budget|under|below|kaunsa)\b", data.question, re.I
    ):
        return result
    selected = next(
        (item for item in data.products if item.product.product_id == data.selected_product_id),
        None,
    )
    if selected is None and len(data.products) == 1:
        selected = data.products[0]
    if (
        selected is None
        or not 0
        <= (utcnow() - selected.product.observed_at).total_seconds()
        <= svc.settings.AI_MAX_SNAPSHOT_AGE_SECONDS
    ):
        return result
    count = UsageRepository().increment_and_get_count(db, current.id, utcnow().date(), "ai_daily")
    if count > svc.settings.MAX_AI_CALLS_PER_USER_PER_DAY:
        db.rollback()
        return result.model_copy(
            update={
                "follow_up": "AI limit reached; exact captured-spec questions remain available."
            }
        )
    # Provider waits hold no database connection and write no page content/history.
    db.commit()
    trace = inference_fallback.set(None)
    try:
        response = await svc.runtime.conversation.orchestrator.ai.decide(
            AIRequest(
                request_id=uuid.uuid4().hex,
                message=data.question,
                language=data.language,
                product=selected.product,
                available_tools=[],
            )
        )
        enriched = selected_fact_reply(
            data, response, now=utcnow(), max_age_seconds=svc.settings.AI_MAX_SNAPSHOT_AGE_SECONDS
        )
        return (enriched or result).model_copy(
            update={"inference_fallback": inference_fallback.get()}
        )
    except Exception:
        return result.model_copy(update={"inference_fallback": inference_fallback.get()})
    finally:
        inference_fallback.reset(trace)


@router.get("/capabilities", response_model=Capabilities)
def capabilities(current: User = Depends(get_current_user)) -> dict:
    """Advertise configuration, not live upstream health or internal grants."""
    config = get_settings()
    return {
        "provider": config.AI_RUNTIME_PROVIDER,
        "analysis_only": config.AI_ANALYSIS_ONLY,
        "local_fallback": config.AI_LOCAL_FALLBACK
        if config.AI_RUNTIME_PROVIDER == "local"
        else None,
        "authentication": config.AUTH_MODE,
        "mutations_enabled": config.AI_MUTATIONS_ENABLED and not config.AI_ANALYSIS_ONLY,
        "browser_origins": list(config.AI_BROWSER_ORIGINS),
        "speech_to_text_configured": bool(config.AI_STT_ENDPOINT and config.AI_STT_MODEL),
        "text_to_speech_configured": bool(config.AI_TTS_ENDPOINT and config.AI_TTS_MODEL),
        "limits": {
            "json_bytes": 262_144,
            "audio_bytes": 5_000_000,
            "events_per_user_per_day": config.MAX_AI_CALLS_PER_USER_PER_DAY,
            "speech_per_user_per_day": config.MAX_SPEECH_CALLS_PER_USER_PER_DAY,
        },
    }


async def process(svc, db, owner, conversation_id, data, response, **kwargs):
    try:
        result, replay = await svc.process(db, owner, conversation_id, data, **kwargs)
    except ValidationError:
        db.rollback()
        reject("INVALID_RUNTIME_INPUT", 422)
    if replay:
        response.headers["Idempotent-Replay"] = "true"
    return result


@router.post("/sessions/{session_id}/conversation", response_model=PublicReply)
async def start(
    session_id: uuid.UUID,
    data: StartConversation,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return await svc.start(db, current.id, session_id, data)


@router.get("/conversations/{conversation_id}", response_model=PublicReply)
def summary(
    conversation_id: Identifier,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return svc.summary(db, current.id, conversation_id)


@router.post("/conversations/{conversation_id}/events", response_model=PublicReply)
async def event(
    conversation_id: Identifier,
    data: RuntimeEvent,
    response: Response,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return await process(svc, db, current.id, conversation_id, data, response)


@router.post("/conversations/{conversation_id}/confirmations", response_model=PublicReply)
async def confirm(
    conversation_id: Identifier,
    data: Confirmation,
    response: Response,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return await process(svc, db, current.id, conversation_id, data, response, confirmation=True)


@router.get("/conversations/{conversation_id}/work", response_model=list[PublicWork])
def work(
    conversation_id: Identifier,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return svc.work(db, current.id, conversation_id)


@router.post(
    "/conversations/{conversation_id}/work/{dispatch_id}/claim", response_model=ClaimedWork
)
def claim(
    conversation_id: Identifier,
    dispatch_id: Identifier,
    data: ClaimWork,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return svc.claim(db, current.id, conversation_id, dispatch_id, data)


@router.post(
    "/conversations/{conversation_id}/work/{dispatch_id}/receipts", response_model=PublicReply
)
async def receipt(
    conversation_id: Identifier,
    dispatch_id: Identifier,
    data: Receipt,
    response: Response,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    return await process(
        svc, db, current.id, conversation_id, data, response, receipt_id=dispatch_id
    )


@router.post(
    "/conversations/{conversation_id}/audio",
    response_model=PublicReply,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("audio/wav", "audio/webm", "audio/ogg", "audio/mpeg")
            },
        }
    },
)
async def audio(
    conversation_id: Identifier,
    request: Request,
    response: Response,
    event_id: Annotated[
        str, Header(alias="X-Event-ID", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    ],
    expected_version: Annotated[int, Header(alias="X-Expected-Version", ge=0)],
    tab_id: Annotated[int | None, Query(ge=0)] = None,
    document_id: Annotated[str | None, Query(max_length=100)] = None,
    origin: Annotated[str | None, Query(max_length=200)] = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    formats = {"audio/wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3"}
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in formats:
        reject("UNSUPPORTED_AUDIO_TYPE", 415)
    try:
        if (tab_id is None) != (document_id is None):
            reject("AUDIO_TARGET_REQUIRED", 422)
        target = (
            BrowserTarget(tab_id=tab_id, document_id=document_id, active=True, window_focused=True)
            if tab_id is not None
            else None
        )
        data = RuntimeEvent(
            event_id=event_id,
            expected_version=expected_version,
            kind="text",
            target=target,
            origin=origin,
        )
        clip = AudioClip(format=formats[content_type], data=await request.body())
        raw = clip.data
        valid = (
            (
                clip.format == "wav"
                and len(raw) >= 12
                and raw[:4] == b"RIFF"
                and raw[8:12] == b"WAVE"
            )
            or (clip.format == "webm" and raw.startswith(b"\x1aE\xdf\xa3"))
            or (clip.format == "ogg" and raw.startswith(b"OggS"))
            or (
                clip.format == "mp3"
                and (
                    raw.startswith(b"ID3")
                    or len(raw) >= 2
                    and raw[0] == 255
                    and raw[1] & 224 == 224
                )
            )
        )
        if not valid:
            reject("INVALID_AUDIO_CONTAINER", 422)
    except ValidationError:
        reject("INVALID_AUDIO_INPUT", 422)
    return await process(svc, db, current.id, conversation_id, data, response, audio=clip)


@router.post(
    "/conversations/{conversation_id}/speech",
    response_class=Response,
    responses={
        200: {
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("audio/wav", "audio/mpeg", "audio/ogg", "audio/webm")
            }
        }
    },
)
async def speech(
    conversation_id: Identifier,
    data: SpeechRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    clip = await svc.speech(db, current.id, conversation_id, data.expected_version)
    types = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "webm": "audio/webm"}
    return Response(
        clip.data,
        media_type=types[clip.format],
        headers={
            "Cache-Control": "no-store",
            "X-Conversation-Version": str(data.expected_version),
        },
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: Identifier,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    svc=Depends(service),
):
    svc.delete(db, current.id, conversation_id)
    return Response(status_code=204)
