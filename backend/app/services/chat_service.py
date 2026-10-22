import concurrent.futures
import datetime
import decimal
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

from app.ai.contracts import (
    AIProvider,
    AIProviderError,
    AIRequest,
    Answer,
    Clarification,
    ShoppingContextContract,
    ToolProposal,
)
from app.config import get_settings
from app.models.session import Message
from app.models.user import User
from app.policies.proposal_policy import ProposalPolicyEngine
from app.repositories.alert_repo import AlertRepository
from app.repositories.context_repo import ContextRepository
from app.repositories.review_repo import ReviewRepository
from app.repositories.session_repo import SessionRepository
from app.repositories.snapshot_repo import SnapshotRepository
from app.repositories.usage_repo import UsageRepository
from app.schemas.alert import PriceAlertRead
from app.schemas.context import RejectionCreate, ShoppingContextUpdate
from app.schemas.message import MessageCreate
from app.services.alert_service import AlertService
from app.services.context_service import ContextService
from app.services.snapshot_service import SnapshotService

settings = get_settings()


def extract_money_amounts(text: str) -> List[decimal.Decimal]:
    """Extract currency values matching ₹, Rs, or INR patterns."""
    amounts: List[decimal.Decimal] = []
    pattern = r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        cleaned = match.group(1).replace(",", "")
        try:
            amounts.append(decimal.Decimal(cleaned))
        except decimal.InvalidOperation:
            pass
    return amounts


def is_grounded(
    extracted_amounts: List[decimal.Decimal],
    allowed_amounts: Set[decimal.Decimal],
) -> bool:
    """Check that every extracted currency amount matches one of the known factual amounts."""
    for amt in extracted_amounts:
        matched = any(abs(amt - allowed) < decimal.Decimal("0.01") for allowed in allowed_amounts)
        if not matched:
            return False
    return True


def build_templated_answer(
    snapshot_price: Optional[decimal.Decimal],
    price_history: Optional[Any],
) -> str:
    """Build fallback answer using strictly verified facts when AI output fails grounding."""
    parts: List[str] = []
    if snapshot_price is not None:
        parts.append(f"The current price is ₹{snapshot_price}.")
    if price_history and getattr(price_history, "count", 0) >= 3:
        parts.append(
            f"Over the last 30 days, the lowest recorded price was ₹{price_history.min}, "
            f"the highest was ₹{price_history.max}, and the average was ₹{price_history.average}."
        )
    elif price_history and getattr(price_history, "count", 0) < 3:
        parts.append("There is no price history yet for this item.")
    else:
        parts.append("Price history is currently unavailable.")
    return " ".join(parts)


class ChatService:
    def __init__(
        self,
        session_repo: Optional[SessionRepository] = None,
        snapshot_repo: Optional[SnapshotRepository] = None,
        snapshot_service: Optional[SnapshotService] = None,
        alert_service: Optional[AlertService] = None,
        alert_repo: Optional[AlertRepository] = None,
        context_repo: Optional[ContextRepository] = None,
        context_service: Optional[ContextService] = None,
        review_repo: Optional[ReviewRepository] = None,
        usage_repo: Optional[UsageRepository] = None,
        policy_engine: Optional[ProposalPolicyEngine] = None,
    ):
        self.session_repo = session_repo or SessionRepository()
        self.snapshot_repo = snapshot_repo or SnapshotRepository()
        self.snapshot_service = snapshot_service or SnapshotService()
        self.alert_service = alert_service or AlertService()
        self.alert_repo = alert_repo or AlertRepository()
        self.context_repo = context_repo or ContextRepository()
        self.context_service = context_service or ContextService(
            context_repo=self.context_repo,
            session_repo=self.session_repo,
            snapshot_repo=self.snapshot_repo,
        )
        self.review_repo = review_repo or ReviewRepository()
        self.usage_repo = usage_repo or UsageRepository()
        self.policy_engine = policy_engine or ProposalPolicyEngine(alert_repo=self.alert_repo)

    def process_message(
        self,
        db: Session,
        user: User,
        session_id: uuid.UUID,
        data: MessageCreate,
        ai_provider: AIProvider,
    ) -> Tuple[Optional[Message], bool, Optional[str]]:
        """
        Process chat message idempotently and orchestrate AI interactions:
        - Replays existing response if request_id matches (does not hit AI limits).
        - Gathers snapshot facts, real price history stats, and recent dialogue.
        - Gathers shopping context and latest review summary.
        - Enforces daily AI limit on usage_counters ("ai_daily").
        - Invokes AIProvider with timeout.
        - Emits safe fallback reply (reply.kind = 'fallback') on error/timeout.
        - Runs grounding check on Answer text (including budget_max).
        - Validates ToolProposal through policies and executes server-side tool once.
        """
        # 1. Idempotency check: if request_id already exists for this user, replay stored reply
        existing_msg = self.session_repo.get_message_by_user_and_request_id(
            db,
            user_id=user.id,
            request_id=data.request_id,
        )
        if existing_msg:
            stored_reply = self.session_repo.get_assistant_reply_following(db, existing_msg)
            if stored_reply:
                return stored_reply, True, None

        # 2. Verify session exists and belongs to this user
        session = self.session_repo.get_session_by_id(db, session_id=session_id, user_id=user.id)
        if not session:
            return None, False, "session_not_found"

        # 3. Retrieve shopping context
        ctx = self.context_service.get_context(db, session_id=session.id, user_id=user.id)
        rejected_titles = self.context_repo.get_rejected_listing_titles(db, context_id=ctx.id)
        context_contract = ShoppingContextContract(
            budget_max=ctx.budget_max,
            preferences=dict(ctx.preferences),
            rejected_titles=rejected_titles,
        )

        # 4. Retrieve snapshot facts, price history stats, and review summary if snapshot_id is provided
        snapshot = None
        listing_id = None
        snapshot_facts: Optional[Dict[str, Any]] = None
        price_summary_dict: Optional[Dict[str, Any]] = None
        price_history = None
        latest_review_summary: Optional[Dict[str, Any]] = None

        if data.snapshot_id:
            snapshot = self.snapshot_repo.get_snapshot_by_id(
                db,
                snapshot_id=data.snapshot_id,
                user_id=user.id,
            )
            if not snapshot:
                return None, False, "snapshot_not_found"
            listing_id = snapshot.listing_id
            snapshot_facts = {
                "title": snapshot.listing.title if snapshot.listing else None,
                "price": str(snapshot.captured_price),
                "availability": snapshot.availability,
                "retailer": snapshot.listing.retailer if snapshot.listing else None,
                "product_id": snapshot.listing.retailer_product_id if snapshot.listing else None,
            }

            history_resp = self.snapshot_service.get_price_history(db, listing_id=listing_id, days=30)
            if history_resp:
                price_history = history_resp
                span_days: float = 0.0
                obs_list = self.snapshot_repo.get_all_accepted_observations(db, listing_id=listing_id)
                if len(obs_list) >= 2:
                    ts_list = [(o.recorded_at or o.captured_at) for o in obs_list]
                    span_seconds = (max(ts_list) - min(ts_list)).total_seconds()
                    if span_seconds < 86400:
                        span_days = round(span_seconds / 86400.0, 2)
                    else:
                        span_days = round(span_seconds / 86400.0, 1)

                price_summary_dict = {
                    "verdict": history_resp.verdict,
                    "reason": history_resp.reason,
                    "span": span_days,
                    "span_days": span_days,
                    "tracking_since": history_resp.tracking_since.isoformat() if history_resp.tracking_since else None,
                    "lowest_price": str(history_resp.min) if history_resp.min is not None else None,
                    "highest_price": str(history_resp.max) if history_resp.max is not None else None,
                    "average_price": str(history_resp.average) if history_resp.average is not None else None,
                    "observation_count": history_resp.count,
                }

            # Retrieve latest stored review summary for this listing
            latest_summary_row = self.review_repo.get_latest_summary_for_listing(db, listing_id=listing_id)
            if latest_summary_row:
                latest_review_summary = latest_summary_row.summary

        # 5. Fetch recent messages for session context
        recent_msgs = self.session_repo.get_recent_messages(
            db,
            session_id=session.id,
            limit=settings.CHAT_RECENT_MESSAGES_COUNT,  # PLACEHOLDER: 10
        )
        recent_payload = [{"role": m.role, "content": m.content} for m in recent_msgs]

        # 6. Persist user message
        self.session_repo.create_message(
            db,
            session_id=session.id,
            user_id=user.id,
            role="user",
            content=data.text,
            request_id=data.request_id,
            kind="user",
            payload=None,
        )

        # 7. Check daily AI call limit (A2)
        server_now = datetime.datetime.now(datetime.timezone.utc)
        ai_count = self.usage_repo.increment_and_get_count(
            db, user_id=user.id, day=server_now.date(), key="ai_daily"
        )
        if ai_count > settings.MAX_AI_CALLS_PER_USER_PER_DAY:
            return None, False, "rate_limited"

        # 8. Build pure contract AIRequest
        ai_req = AIRequest(
            user_text=data.text,
            snapshot_facts=snapshot_facts,
            price_summary=price_summary_dict,
            recent_messages=recent_payload,
            preferences=dict(ctx.preferences),
            context=context_contract,
            review_summary=latest_review_summary,
        )

        # 9. Call AIProvider with timeout
        ai_resp = None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(ai_provider.decide, ai_req)
                ai_resp = future.result(timeout=settings.AI_TIMEOUT_SECONDS)  # PLACEHOLDER: 5.0
        except Exception:
            # Fallback on AIProviderError, TimeoutError, or any execution failure
            reply_text = "I'm having trouble analyzing this product right now. Please try again in a moment."
            assistant_msg = self.session_repo.create_message(
                db,
                session_id=session.id,
                user_id=user.id,
                role="assistant",
                content=reply_text,
                kind="fallback",
                payload=None,
            )
            db.commit()
            db.refresh(assistant_msg)
            return assistant_msg, False, None

        # Check for malformed output
        if not isinstance(ai_resp, (Answer, Clarification, ToolProposal)):
            reply_text = "I'm having trouble analyzing this product right now. Please try again in a moment."
            assistant_msg = self.session_repo.create_message(
                db,
                session_id=session.id,
                user_id=user.id,
                role="assistant",
                content=reply_text,
                kind="fallback",
                payload=None,
            )
            db.commit()
            db.refresh(assistant_msg)
            return assistant_msg, False, None

        # 10. Orchestrate decision
        reply_content: str = ""
        reply_kind: str = "answer"
        reply_payload: Optional[Dict[str, Any]] = None

        if isinstance(ai_resp, Answer):
            reply_kind = "answer"
            reply_content = ai_resp.text

            # Grounding check: verify all money mentions against verified facts & budget_max
            extracted = extract_money_amounts(ai_resp.text)
            if extracted:
                allowed_amounts: Set[decimal.Decimal] = set()
                if snapshot and snapshot.captured_price:
                    allowed_amounts.add(snapshot.captured_price)
                if price_history:
                    if price_history.min is not None:
                        allowed_amounts.add(price_history.min)
                    if price_history.max is not None:
                        allowed_amounts.add(price_history.max)
                    if price_history.average is not None:
                        allowed_amounts.add(price_history.average)

                # Include budget_max in allowed grounding amounts
                if ctx and ctx.budget_max is not None:
                    allowed_amounts.add(ctx.budget_max)

                # Include any existing alert targets for this user
                user_alerts = self.alert_repo.list_alerts(db, user_id=user.id, status="active")
                for al in user_alerts:
                    allowed_amounts.add(al.target_price)

                if not is_grounded(extracted, allowed_amounts):
                    # Replace with factual templated answer
                    reply_content = build_templated_answer(
                        snapshot_price=snapshot.captured_price if snapshot else None,
                        price_history=price_history,
                    )

        elif isinstance(ai_resp, Clarification):
            reply_kind = "clarification"
            reply_content = ai_resp.question

        elif isinstance(ai_resp, ToolProposal):
            # Check if context was updated concurrently
            current_db_ctx = self.context_repo.get_context_by_id(db, context_id=ctx.id, user_id=user.id)
            context_stale = (current_db_ctx.version != ctx.version) if current_db_ctx else False

            context = {
                "listing_id": listing_id,
                "current_price": snapshot.captured_price if snapshot else None,
                "context_stale_version": context_stale,
            }
            policy_result = self.policy_engine.validate_proposal(
                proposal=ai_resp,
                user=user,
                context=context,
                db=db,
            )

            if policy_result.action == "rejected":
                reply_kind = "answer"
                reply_content = f"I couldn't complete that request: {policy_result.reason}."
            elif policy_result.action == "clarification":
                reply_kind = "clarification"
                reply_content = (
                    policy_result.clarification_text
                    or "Could you please clarify your request?"
                )
            elif policy_result.action == "allowed":
                if ai_resp.tool == "create_price_alert":
                    if not listing_id:
                        reply_kind = "answer"
                        reply_content = "Please view a product snapshot before creating a price alert."
                    else:
                        target = decimal.Decimal(str(ai_resp.args["target_price"]))
                        created_alert, alert_err = self.alert_service.create_alert(
                            db,
                            user=user,
                            listing_id=listing_id,
                            target_price=target,
                        )
                        if created_alert:
                            alert_read = PriceAlertRead.model_validate(created_alert)
                            alert_dict = alert_read.model_dump(mode="json")
                            reply_kind = "tool"
                            reply_content = f"I've set a price alert for you at ₹{target}."
                            reply_payload = {"alert": alert_dict}
                        else:
                            reply_kind = "answer"
                            reply_content = f"Unable to create price alert: {alert_err}."

                elif ai_resp.tool == "set_budget":
                    budget_val = decimal.Decimal(str(ai_resp.args["budget_max"]))
                    self.context_service.update_context(
                        db,
                        session_id=session.id,
                        user_id=user.id,
                        data=ShoppingContextUpdate(budget_max=budget_val, version=ctx.version),
                    )
                    reply_kind = "tool"
                    reply_content = f"I've updated your budget to ₹{budget_val}."
                    reply_payload = {"budget": {"budget_max": str(budget_val)}}

                elif ai_resp.tool == "set_preference":
                    key = ai_resp.args.get("key", "")
                    value = ai_resp.args.get("value", "")
                    self.context_service.update_context(
                        db,
                        session_id=session.id,
                        user_id=user.id,
                        data=ShoppingContextUpdate(preferences={key: value}, version=ctx.version),
                    )
                    reply_kind = "tool"
                    reply_content = f"Preference '{key}' has been set to '{value}'."
                    reply_payload = {"preference": {"key": key, "value": value}}

                elif ai_resp.tool == "reject_current_product":
                    reason = ai_resp.args.get("reason")
                    self.context_service.add_rejection(
                        db,
                        session_id=session.id,
                        user_id=user.id,
                        data=RejectionCreate(listing_id=listing_id, reason=reason),
                    )
                    reply_kind = "tool"
                    reply_content = "I've noted that you don't want this product and marked it as rejected."
                    reply_payload = {"rejection": {"listing_id": str(listing_id), "reason": reason}}

        # 11. Store assistant message and return
        assistant_msg = self.session_repo.create_message(
            db,
            session_id=session.id,
            user_id=user.id,
            role="assistant",
            content=reply_content,
            kind=reply_kind,
            payload=reply_payload,
        )
        db.commit()
        db.refresh(assistant_msg)
        return assistant_msg, False, None
