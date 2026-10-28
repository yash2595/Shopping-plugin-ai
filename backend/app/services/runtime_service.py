"""Authenticated durable application boundary around the existing pure AI runtime."""

import asyncio
import hashlib
import json
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from app.repositories.ai_checkpoint_repo import AICheckpointRepository
from app.repositories.runtime_repo import RuntimeRepository, utcnow
from app.repositories.usage_repo import UsageRepository
from app.schemas.runtime import PublicReply
from fastapi import HTTPException
from shopping_ai.browser import BrowserResult
from shopping_ai.checkpoint import SCHEMA_VERSION, CheckpointError, load_checkpoint
from shopping_ai.conversation import ConversationError, ConversationTurn
from shopping_ai.market import SearchReceipt
from shopping_ai.models import MutationAuthorization
from shopping_ai.navigation import NavigationReceipt
from shopping_ai.orchestrator import OrchestrationError, Principal
from shopping_ai.runtime import (
    AdvanceEvent,
    AudioEvent,
    BrowserEvent,
    CancelEvent,
    ExpireEvent,
    MarketEvent,
    NavigationEvent,
    ResumeEvent,
    TextEvent,
)
from shopping_ai.voice import AudioClip, VoiceTurn


def reject(code, status=409):
    raise HTTPException(
        status_code=status, detail={"code": code, "message": code.replace("_", " ").capitalize()}
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class RuntimeService:
    def __init__(self, runtime, settings, *, repository=None, checkpoints=None):
        self.runtime, self.settings = runtime, settings
        self.repo = repository or RuntimeRepository()
        self.checkpoints = checkpoints or AICheckpointRepository()
        self.speech_capacity = asyncio.Semaphore(settings.AI_MAX_CONCURRENT_CALLS)

    def owned(self, db, owner, conversation_id):
        row = self.repo.conversation(db, owner, conversation_id)
        if row is None:
            reject("CONVERSATION_NOT_FOUND", 404)
        principal = Principal(owner_id=str(owner), session_id=str(row.session_id))
        # Reply metadata and checkpoint must come from the same SQL row snapshot;
        # a second SELECT could pair an old reply with a newer committed version.
        if row.schema_version != SCHEMA_VERSION:
            raise CheckpointError("CHECKPOINT_SCHEMA_UNSUPPORTED")
        state = load_checkpoint(
            row.checkpoint.encode("utf-8"),
            principal,
            conversation_id=conversation_id,
            expected_version=row.version,
        )
        return row, principal, state

    async def start(self, db, owner, session_id, data):
        if self.repo.session(db, owner, session_id, lock=True) is None:
            reject("SESSION_NOT_FOUND", 404)
        existing = self.repo.for_session(db, owner, session_id)
        if existing:
            _, _, state = self.owned(db, owner, existing.conversation_id)
            if (state.language, state.currency) != (data.language, data.currency):
                reject("CONVERSATION_SETTINGS_CONFLICT")
            return self.summary(db, owner, existing.conversation_id)
        principal = Principal(owner_id=str(owner), session_id=str(session_id))
        output = await self.runtime.start(principal, language=data.language, currency=data.currency)
        self.checkpoints.create(db, principal, output.state)
        result = self.public(output.conversation_reply, None, None)
        self.repo.public_reply(db, owner, output.state.conversation_id, result)
        db.commit()
        return result

    def summary(self, db, owner, conversation_id):
        row, principal, state = self.owned(db, owner, conversation_id)
        if row.public_reply:
            return PublicReply.model_validate(row.public_reply).model_dump(mode="json")
        return PublicReply(
            conversation_id=conversation_id,
            session_id=uuid.UUID(principal.session_id),
            version=state.version,
            status="chat",
            message="Conversation restored.",
            speech_text="Conversation restored.",
            products=state.products,
        ).model_dump(mode="json")

    def origin(self, supplied, saved=None, *, required=False):
        value = supplied or saved
        allowed = set(self.settings.AI_BROWSER_ORIGINS) | {
            store.origin for store in self.runtime.conversation.stores
        }
        if value and value not in allowed:
            reject("UNSUPPORTED_SITE", 403)
        if required and not value:
            reject("BROWSER_ORIGIN_REQUIRED", 422)
        return value

    def context(self, state, target, origin, saved_origin):
        origin = self.origin(origin, saved_origin, required=target is not None)
        if state.active_run and (state.active_run.pending or state.active_run.recovery_required):
            if saved_origin and origin != saved_origin:
                reject("RECOVERY_ORIGIN_MISMATCH")
        return origin

    def tools(self):
        if self.settings.AI_MUTATIONS_ENABLED:
            return ("read_product", "read_cart", "select_variant", "add_to_cart")
        return ("read_product", "read_cart")

    def public(self, reply, event_id, origin):
        work = []
        if reply.action:
            work.append(reply.action.action_id)
        if reply.navigation:
            work.append(reply.navigation.action_id)
        work.extend(task.task_id for task in reply.search_tasks)
        return PublicReply(
            conversation_id=reply.state.conversation_id,
            session_id=uuid.UUID(reply.state.principal.session_id),
            version=reply.state.version,
            event_id=event_id,
            status=reply.status,
            message=reply.message,
            speech_text=reply.speech_text,
            facts=reply.facts,
            products=reply.state.products,
            comparison=reply.comparison,
            market_result=reply.market_result,
            market_answer=reply.market_answer,
            work_ids=tuple(work),
            origin=origin,
            speech_available=self.runtime.voice is not None,
        ).model_dump(mode="json")

    def proposals(self, reply):
        work = []
        if reply.action:
            work.append(
                (
                    reply.action.action_id,
                    "browser",
                    reply.action.model_dump(mode="json"),
                    reply.action.expires_at,
                )
            )
        if reply.navigation:
            work.append(
                (
                    reply.navigation.action_id,
                    "navigation",
                    reply.navigation.model_dump(mode="json", exclude={"owner_id", "session_id"}),
                    reply.navigation.expires_at,
                )
            )
        for task in reply.search_tasks:
            work.append((task.task_id, "market", task.model_dump(mode="json"), task.deadline_at))
        return work

    def persist(self, db, principal, previous, reply, event_id, origin):
        if not self.checkpoints.compare_and_swap(
            db, principal, reply.state, expected_version=previous.version
        ):
            reject("VERSION_CONFLICT")
        grant = reply.state.active_run.request.authorization if reply.state.active_run else None
        constraints = (
            {"max_unit_price": str(grant.max_unit_price), "currency": grant.currency}
            if grant
            else None
        )
        result = self.public(reply, event_id, origin)
        self.repo.public_reply(db, uuid.UUID(principal.owner_id), previous.conversation_id, result)
        self.repo.reconcile_outbox(
            db, principal, reply.state, origin, self.proposals(reply), constraints
        )
        return result

    def replay(self, event, payload_hash):
        if event.payload_hash != payload_hash:
            reject("IDEMPOTENCY_CONFLICT")
        if event.status == "complete":
            return PublicReply.model_validate(event.result).model_dump(mode="json"), True
        if event.status == "failed":
            reject(event.result["code"], event.result["status"])
        return None

    async def process(
        self, db, owner, conversation_id, data, *, confirmation=False, receipt_id=None, audio=None
    ):
        row, principal, state = self.owned(db, owner, conversation_id)
        saved_origin = (row.public_reply or {}).get("origin")
        raw = data.model_dump(mode="json")
        if audio is not None:
            raw["audio_hash"] = hashlib.sha256(audio.data).hexdigest()
            raw["audio_format"] = audio.format
        raw["operation"] = (
            "receipt"
            if receipt_id
            else "confirmation"
            if confirmation
            else "audio"
            if audio
            else "event"
        )
        raw["dispatch_id"] = receipt_id
        payload_hash = digest(raw)
        event = self.repo.event(db, owner, conversation_id, data.event_id)
        if event:
            replay = self.replay(event, payload_hash)
            if replay:
                return replay
            if event.lease_until > utcnow():
                reject("REQUEST_IN_PROGRESS")
        if state.version != data.expected_version:
            reject("VERSION_CONFLICT")
        # Validate all bindings before durable admission or paid provider work.
        receipt_hash = None
        if receipt_id:
            dispatch = self.repo.dispatch(db, owner, conversation_id, receipt_id)
            runtime_event = self.receipt_event(dispatch, state, principal, data)
            origin = data.origin
            receipt_hash = digest(raw)
        elif confirmation:
            origin = self.context(state, data.target, data.origin, saved_origin)
            runtime_event = self.confirm(state, data, origin)
        else:
            # Stop remains available after navigation/config changes; it must not
            # depend on the current page still being a supported shopping origin.
            origin = (
                saved_origin
                if audio is None and data.kind == "cancel"
                else self.context(state, data.target, data.origin, saved_origin)
            )
            if audio is not None:
                if self.runtime.voice is None:
                    reject("VOICE_UNAVAILABLE", 503)
                runtime_event = AudioEvent(
                    turn=VoiceTurn(
                        request_id=data.event_id,
                        audio=audio,
                        target=data.target,
                        available_tools=self.tools(),
                    )
                )
            elif data.kind == "text":
                if not data.message or not data.message.strip():
                    reject("MESSAGE_REQUIRED", 422)
                runtime_event = TextEvent(
                    turn=ConversationTurn(
                        request_id=data.event_id,
                        message=data.message,
                        target=data.target,
                        available_tools=self.tools(),
                    )
                )
            elif data.kind == "cancel":
                runtime_event = CancelEvent(request_id=data.event_id)
            elif data.kind == "resume":
                if data.target is None:
                    reject("TARGET_REQUIRED", 422)
                runtime_event = ResumeEvent(target=data.target)
            else:
                runtime_event = AdvanceEvent()
        seconds = self.settings.AI_RUNTIME_TIMEOUT_SECONDS + 15
        token = (
            self.repo.reclaim(db, event, seconds)
            if event
            else self.repo.admit(
                db,
                owner,
                uuid.UUID(principal.session_id),
                conversation_id,
                data.event_id,
                payload_hash,
                data.expected_version,
                seconds,
            )
        )
        if token is None:
            db.rollback()
            reject("REQUEST_IN_PROGRESS")
        read_recovery = (
            isinstance(runtime_event, AdvanceEvent)
            and state.active_run is not None
            and state.active_run.recovery_required
        )
        if isinstance(runtime_event, (TextEvent, AudioEvent, AdvanceEvent)) and not read_recovery:
            count = UsageRepository().increment_and_get_count(
                db, owner, utcnow().date(), "ai_daily"
            )
            if count > self.settings.MAX_AI_CALLS_PER_USER_PER_DAY:
                db.rollback()
                reject("RATE_LIMITED", 429)
        db.commit()
        try:
            async with asyncio.timeout(self.settings.AI_RUNTIME_TIMEOUT_SECONDS):
                output = await self.runtime.handle(
                    state,
                    principal,
                    runtime_event,
                    expected_version=state.version,
                    defer_speech=isinstance(runtime_event, AudioEvent),
                )
            reply = output.conversation_reply
            if reply is None:
                if output.reply.status == "needs_input":
                    reject("TRANSCRIPTION_UNCLEAR", 422)
                reject("TRANSCRIPTION_UNAVAILABLE", 503)
            if self.repo.session(db, owner, uuid.UUID(principal.session_id), lock=True) is None:
                reject("SESSION_NOT_FOUND", 404)
            locked = self.repo.event(db, owner, conversation_id, data.event_id, lock=True)
            if (
                locked is None
                or locked.status != "processing"
                or locked.lease_token != token
                or locked.lease_until <= utcnow()
            ):
                reject("EVENT_LEASE_LOST")
            result = self.persist(db, principal, state, reply, data.event_id, origin)
            if receipt_id:
                accepted = self.repo.dispatch(db, owner, conversation_id, receipt_id, lock=True)
                if accepted is None or accepted.receipt_hash is not None:
                    reject("RECEIPT_CONFLICT")
                accepted.status, accepted.receipt_hash = "received", receipt_hash
            locked.status, locked.result = "complete", result
            db.commit()
            return result, False
        except (HTTPException, ConversationError, OrchestrationError, CheckpointError) as exc:
            db.rollback()
            if isinstance(exc, HTTPException):
                code, status = exc.detail["code"], exc.status_code
            else:
                code, status = str(exc), 409
            self.repo.fail(
                db, owner, conversation_id, data.event_id, token, {"code": code, "status": status}
            )
            db.commit()
            reject(code, status)
        except ValueError:
            # Core navigation/market validators use safe ValueError boundaries.
            # Never expose their exception text (Pydantic may embed private input).
            db.rollback()
            evidence = isinstance(runtime_event, (BrowserEvent, MarketEvent, NavigationEvent))
            code, status = (
                ("INVALID_RUNTIME_EVIDENCE", 409) if evidence else ("RUNTIME_UNAVAILABLE", 503)
            )
            self.repo.fail(
                db, owner, conversation_id, data.event_id, token, {"code": code, "status": status}
            )
            db.commit()
            reject(code, status)
        except Exception:
            db.rollback()
            self.repo.fail(
                db,
                owner,
                conversation_id,
                data.event_id,
                token,
                {"code": "RUNTIME_UNAVAILABLE", "status": 503},
            )
            db.commit()
            reject("RUNTIME_UNAVAILABLE", 503)

    def confirm(self, state, data, origin):
        if not self.settings.AI_MUTATIONS_ENABLED:
            reject("MUTATIONS_DISABLED", 403)
        product = next(
            (
                p
                for p in state.products
                if p.product_id == data.product_id and p.snapshot_id == data.snapshot_id
            ),
            None,
        )
        if (
            product is None
            or state.selected_id != data.product_id
            or not data.target.visible
            or product.document_id != data.target.document_id
            or not 0
            <= (utcnow() - product.observed_at).total_seconds()
            <= self.runtime.conversation.orchestrator.ai.config.max_snapshot_age_seconds
            or data.variant_id not in {v.variant_id for v in product.variants}
            or product.currency != data.currency
            or product.price is None
            or product.price > data.max_unit_price
            or product.in_stock is not True
        ):
            reject("CONFIRMATION_STALE_OR_MISMATCHED")
        if state.active_run and (state.active_run.pending or state.active_run.recovery_required):
            reject("RECONCILIATION_REQUIRED")
        grant = MutationAuthorization(
            request_id=data.event_id,
            allowed_tools=["select_variant"]
            if data.operation == "select_variant"
            else ["select_variant", "add_to_cart"],
            product_id=product.product_id,
            document_id=product.document_id,
            variant_id=data.variant_id,
            quantity=data.quantity,
            mode=data.mode,
            max_unit_price=data.max_unit_price,
            currency=data.currency,
            expires_at=utcnow() + timedelta(seconds=120),
        )
        return TextEvent(
            turn=ConversationTurn(
                request_id=data.event_id,
                message="select variant" if data.operation == "select_variant" else "add to cart",
                target=data.target,
                available_tools=self.tools(),
                authorization=grant,
            )
        )

    def receipt_event(self, dispatch, state, principal, data):
        if dispatch is None:
            reject("DISPATCH_NOT_FOUND", 404)
        if (
            dispatch.claim_token is None
            or dispatch.claim_token != data.claim_token
            or dispatch.status not in {"claimed", "suppressed"}
            or dispatch.receipt_hash is not None
        ):
            reject("RECEIPT_CLAIM_MISMATCH")
        work = dispatch.payload["work"]
        expected_origin = dispatch.origin
        if dispatch.kind == "market":
            expected_origin = work["store"]["origin"]
        elif dispatch.kind == "navigation":
            url = urlsplit(work["offer"]["url"])
            expected_origin = f"{url.scheme}://{url.netloc}"
        origins = {expected_origin}
        failed_navigation = (
            dispatch.kind == "navigation"
            and data.navigation is not None
            and data.navigation.status == "failed"
        )
        failed_search = (
            dispatch.kind == "market"
            and data.search is not None
            and data.search.status != "complete"
        )
        if failed_navigation or failed_search:
            # A blocked navigation/search can fail before leaving its source page.
            origins.add(dispatch.claim_target.get("origin", dispatch.origin))
        if data.origin not in origins:
            reject("RECEIPT_ORIGIN_MISMATCH")
        if sum(item is not None for item in (data.browser, data.search, data.navigation)) != 1:
            reject("RECEIPT_KIND_MISMATCH", 422)
        if dispatch.kind == "browser" and data.browser is not None:
            observation = data.browser
            if observation.cart is not None and observation.cart_coverage != "complete":
                reject("CART_COVERAGE_INCOMPLETE", 422)
            if observation.target.tab_id != work["target"]["tab_id"]:
                reject("RECEIPT_TARGET_MISMATCH")
            if (
                observation.outcome == "observed"
                and observation.target.document_id != work["target"]["document_id"]
            ):
                reject("RECEIPT_TARGET_MISMATCH")
            return BrowserEvent(
                result=BrowserResult(
                    **observation.model_dump(exclude={"cart_coverage"}),
                    action_id=dispatch.dispatch_id,
                    run_id=work["run_id"],
                    session_id=principal.session_id,
                    run_version=work["run_version"],
                    source_document_id=work["target"]["document_id"],
                )
            )
        if dispatch.kind == "market" and data.search is not None and state.active_market:
            observed = data.search
            if (
                observed.target.tab_id != dispatch.claim_target["tab_id"]
                or not observed.target.visible
                or not 0
                <= (utcnow() - observed.observed_at).total_seconds()
                <= self.runtime.conversation.orchestrator.ai.config.max_snapshot_age_seconds
                or any(offer.observed_at > observed.observed_at for offer in observed.offers)
            ):
                reject("SEARCH_RECEIPT_TARGET_OR_TIME_MISMATCH")
            return MarketEvent(
                receipt=SearchReceipt(
                    **data.search.model_dump(exclude={"target", "observed_at"}),
                    search_id=state.active_market.search_id,
                    owner_id=principal.owner_id,
                    session_id=principal.session_id,
                    task_id=dispatch.dispatch_id,
                    delivery_context=state.active_market.delivery_context,
                )
            )
        if dispatch.kind == "navigation" and data.navigation is not None:
            if (
                data.navigation.target
                and data.navigation.target.tab_id != dispatch.claim_target["tab_id"]
            ):
                reject("RECEIPT_TARGET_MISMATCH")
            return NavigationEvent(
                receipt=NavigationReceipt(
                    **data.navigation.model_dump(),
                    action_id=dispatch.dispatch_id,
                    search_id=work["search_id"],
                    owner_id=principal.owner_id,
                    session_id=principal.session_id,
                )
            )
        reject("RECEIPT_KIND_MISMATCH", 422)

    def work(self, db, owner, conversation_id):
        self.owned(db, owner, conversation_id)
        return [
            {
                "dispatch_id": item.dispatch_id,
                "kind": item.kind,
                "status": item.status,
                "version": item.version,
                "deadline_at": item.deadline_at.isoformat(),
            }
            for item in self.repo.work(db, owner, conversation_id)
        ]

    def claim(self, db, owner, conversation_id, dispatch_id, data):
        row, principal, state = self.owned(db, owner, conversation_id)
        if self.repo.session(db, owner, uuid.UUID(principal.session_id), lock=True) is None:
            reject("SESSION_NOT_FOUND", 404)
        # Re-read after acquiring the lane: cancellation may have committed while waiting.
        row, principal, state = self.owned(db, owner, conversation_id)
        if state.version != data.expected_version:
            reject("VERSION_CONFLICT")
        self.origin(data.origin, required=True)
        item = self.repo.dispatch(db, owner, conversation_id, dispatch_id, lock=True)
        if item is None:
            reject("DISPATCH_NOT_FOUND", 404)
        if (
            item.kind == "browser"
            and item.payload["work"]["tool"]["name"] in {"select_variant", "add_to_cart"}
            and not self.settings.AI_MUTATIONS_ENABLED
        ):
            reject("MUTATIONS_DISABLED", 403)
        if item.deadline_at <= utcnow() or item.status not in {"pending", "claimed"}:
            reject("DISPATCH_UNAVAILABLE")
        source_origin = (
            (row.public_reply or {}).get("origin") if item.kind == "market" else item.origin
        )
        if not data.target.visible or source_origin and data.origin != source_origin:
            reject("DISPATCH_TARGET_MISMATCH")
        if (
            item.kind == "browser"
            and data.target.model_dump(mode="json") != item.payload["work"]["target"]
        ):
            reject("DISPATCH_TARGET_MISMATCH")
        if (
            item.kind == "navigation"
            and data.target.model_dump(mode="json") != item.payload["work"]["source"]
        ):
            reject("DISPATCH_TARGET_MISMATCH")
        if item.status == "claimed":
            if item.claim_id != data.claim_id or item.claim_target != {
                **data.target.model_dump(mode="json"),
                "origin": data.origin,
            }:
                reject("DISPATCH_ALREADY_CLAIMED")
        else:
            if self.repo.active_claim(db, owner, uuid.UUID(principal.session_id), dispatch_id):
                reject("BROWSER_LANE_BUSY")
            item.status, item.claim_id, item.claim_token = "claimed", data.claim_id, uuid.uuid4()
            item.claim_target = {**data.target.model_dump(mode="json"), "origin": data.origin}
        result = {
            "dispatch_id": item.dispatch_id,
            "kind": item.kind,
            "version": item.version,
            "deadline_at": item.deadline_at.isoformat(),
            "claim_token": str(item.claim_token),
            "origin": data.origin,
            **item.payload,
        }
        db.commit()
        return result

    async def settle_due(self, db, limit=20):
        settled = 0
        candidates = [
            (item.user_id, item.session_id, item.conversation_id, item.dispatch_id)
            for item in self.repo.due(db, limit)
        ]
        db.commit()
        for owner, session_id, conversation_id, dispatch_id in candidates:
            if self.repo.session(db, owner, session_id, lock=True, skip_locked=True) is None:
                db.rollback()
                continue
            item = self.repo.dispatch(db, owner, conversation_id, dispatch_id, lock=True)
            if (
                item is None
                or item.status not in {"pending", "claimed"}
                or item.deadline_at > utcnow()
            ):
                db.rollback()
                continue
            _, principal, state = self.owned(db, item.user_id, item.conversation_id)
            current = False
            if item.kind == "browser":
                current = bool(
                    state.active_run
                    and state.active_run.pending
                    and state.active_run.pending.action_id == item.dispatch_id
                )
                event = ExpireEvent(scope="browser")
            elif item.kind == "market":
                current = bool(
                    state.active_market
                    and any(t.task_id == item.dispatch_id for t in state.active_market.tasks)
                    and not any(r.task_id == item.dispatch_id for r in state.active_market.receipts)
                )
                event = ExpireEvent(scope="market")
            else:
                current = bool(
                    state.pending_navigation
                    and state.pending_navigation.action_id == item.dispatch_id
                )
                event = CancelEvent(request_id="deadline." + item.dispatch_id)
            if current:
                output = await self.runtime.handle(
                    state, principal, event, expected_version=state.version
                )
                origin = (
                    self.repo.conversation(db, item.user_id, item.conversation_id).public_reply
                    or {}
                ).get("origin")
                self.persist(
                    db,
                    principal,
                    state,
                    output.conversation_reply,
                    "deadline." + item.dispatch_id,
                    origin,
                )
            item.status = "expired"
            settled += 1
            db.commit()
        return settled

    def delete(self, db, owner, conversation_id):
        _, principal, state = self.owned(db, owner, conversation_id)
        self.repo.session(db, owner, uuid.UUID(principal.session_id), lock=True)
        _, principal, state = self.owned(db, owner, conversation_id)
        if state.active_run and (state.active_run.pending or state.active_run.recovery_required):
            reject("RECONCILIATION_REQUIRED")
        self.repo.delete_conversation(db, owner, conversation_id)
        db.commit()

    async def speech(self, db, owner, conversation_id, version):
        row, principal, state = self.owned(db, owner, conversation_id)
        if state.version != version:
            reject("VERSION_CONFLICT")
        if self.runtime.voice is None or not row.public_reply:
            reject("VOICE_UNAVAILABLE", 503)
        text = PublicReply.model_validate(row.public_reply).speech_text
        count = UsageRepository().increment_and_get_count(
            db, owner, utcnow().date(), "speech_daily"
        )
        if count > self.settings.MAX_SPEECH_CALLS_PER_USER_PER_DAY:
            db.rollback()
            reject("SPEECH_RATE_LIMITED", 429)
        db.commit()
        try:
            async with asyncio.timeout(self.settings.AI_RUNTIME_TIMEOUT_SECONDS):
                async with self.speech_capacity:
                    clip = await self.runtime.voice.synthesizer.synthesize(
                        text, language=state.language
                    )
                    clip = AudioClip(format=clip.format, data=clip.data)
        except Exception:
            reject("SPEECH_UNAVAILABLE", 503)
        _, _, current = self.owned(db, owner, conversation_id)
        if current.version != version:
            reject("SPEECH_SUPERSEDED")
        return clip
