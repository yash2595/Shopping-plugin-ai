"""Versioned AI checkpoints for trusted persistence adapters; no database or auth.

Never accept a client-supplied checkpoint as proof of ownership or authorization.
Read it from an owner-scoped trusted store, then verify its metadata here. Durable
compare-and-swap and dispatch idempotency remain the persistence adapter's job.
"""

import json

from pydantic import Field, StrictInt

from shopping_ai.conversation import ConversationState
from shopping_ai.models import Contract
from shopping_ai.orchestrator import Principal

SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 4_000_000


class CheckpointError(ValueError):
    """Safe code only: validation errors must never echo a saved user payload."""


class Checkpoint(Contract):
    schema_version: StrictInt = Field(default=SCHEMA_VERSION, ge=1, le=1)
    state: ConversationState


def _owner(state: ConversationState, principal: Principal):
    if state.principal != principal:
        raise CheckpointError("CHECKPOINT_ACCESS_DENIED")


def dump_checkpoint(state: ConversationState, principal: Principal) -> bytes:
    try:
        state = ConversationState.model_validate(state.model_dump(warnings=False))
        _owner(state, principal)
        payload = Checkpoint(state=state).model_dump_json().encode("utf-8")
    except CheckpointError:
        raise
    except Exception:
        raise CheckpointError("CHECKPOINT_INVALID") from None
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("CHECKPOINT_TOO_LARGE")
    return payload


def _distinct_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def load_checkpoint(
    payload: bytes,
    principal: Principal,
    *,
    conversation_id: str,
    expected_version: int,
) -> ConversationState:
    if not isinstance(payload, bytes):
        raise CheckpointError("CHECKPOINT_INVALID")
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("CHECKPOINT_TOO_LARGE")
    if type(expected_version) is not int or expected_version < 0:
        raise CheckpointError("CHECKPOINT_VERSION_MISMATCH")
    try:
        data = json.loads(
            payload, object_pairs_hook=_distinct_keys, parse_constant=_reject_constant
        )
        version = data["schema_version"]
        if type(version) is not int or version != SCHEMA_VERSION:
            raise CheckpointError("CHECKPOINT_SCHEMA_UNSUPPORTED")
        checkpoint = Checkpoint.model_validate(data)
    except CheckpointError:
        raise
    except Exception:
        raise CheckpointError("CHECKPOINT_INVALID") from None
    state = checkpoint.state
    _owner(state, principal)
    if state.conversation_id != conversation_id:
        raise CheckpointError("CHECKPOINT_CONVERSATION_MISMATCH")
    if state.version != expected_version:
        raise CheckpointError("CHECKPOINT_VERSION_MISMATCH")
    # No timestamps or action IDs are rewritten during restoration. Existing
    # lifecycle methods still reject expired proposals and stale observations.
    return state
