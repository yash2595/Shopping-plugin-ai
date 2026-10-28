import decimal
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.contracts import (
    CreatePriceAlertArgs,
    RejectCurrentProductArgs,
    SetBudgetArgs,
    SetPreferenceArgs,
    ToolProposal,
)
from app.config import get_settings
from app.models.user import User
from app.repositories.alert_repo import AlertRepository

settings = get_settings()


def explicitly_requested(proposal: ToolProposal, user_text: str) -> bool:
    """Conservative current-command guard, independent of page/model instructions.

    Unrecognized phrasing requests clarification. It must never expand permission
    from a quoted command, an explanation, a prior turn, or a different amount.
    """
    text = user_text.lower().strip().rstrip(".! ")
    text = re.sub(r"^(?:(?:can|could|would) you (?:please )?|please )", "", text)
    amount = r"(?:₹|rs\.?\s*|inr\s*)?([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    if proposal.tool in {"set_budget", "create_price_alert"}:
        prefix = (
            r"(?:my budget is|set (?:my )?budget (?:to|at)|mera budget(?: hai)?)\s+"
            if proposal.tool == "set_budget"
            else r"(?:notify me|alert me|set (?:a )?(?:price )?alert)(?: when (?:the )?price drops)?\s+(?:below|under|at)\s+"
        )
        match = re.fullmatch(prefix + amount, text)
        if not match:
            return False
        key = "budget_max" if proposal.tool == "set_budget" else "target_price"
        return decimal.Decimal(match[1].replace(",", "")) == decimal.Decimal(
            str(proposal.args[key])
        )
    if proposal.tool == "set_preference":
        key, value = proposal.args["key"], str(proposal.args["value"]).lower()
        key_pattern = "colou?r" if key == "color" else re.escape(key.replace("_", " "))
        # Only color has a useful unambiguous shorthand in the offline demo.
        key_prefix = rf"(?:{key_pattern}\s+)?" if key == "color" else rf"{key_pattern}\s+"
        patterns = [
            rf"(?:i prefer|prefer)\s+{key_prefix}{re.escape(value)}",
            rf"set (?:my )?{key_pattern} to {re.escape(value)}",
        ]
        return any(re.fullmatch(pattern, text) for pattern in patterns)
    return bool(
        re.fullmatch(
            r"(?:not this(?: one)?(?:,? too expensive)?|reject (?:this|the current) product|yeh nahi)",
            text,
        )
    )


@dataclass(frozen=True)
class PolicyResult:
    action: Literal["allowed", "rejected", "clarification"]
    reason: Optional[str] = None
    clarification_text: Optional[str] = None


class ProposalPolicyEngine:
    def __init__(self, alert_repo: Optional[AlertRepository] = None):
        self.alert_repo = alert_repo or AlertRepository()

    def validate_proposal(
        self,
        proposal: ToolProposal,
        user: User,
        context: Dict[str, Any],
        db: Session,
    ) -> PolicyResult:
        """
        Validates AI tool proposals against security, business, and user safety bounds.
        """
        if proposal.tool not in (
            "create_price_alert",
            "set_preference",
            "set_budget",
            "reject_current_product",
        ):
            return PolicyResult(action="rejected", reason="Unknown tool")

        argument_types = {
            "create_price_alert": CreatePriceAlertArgs,
            "set_budget": SetBudgetArgs,
            "set_preference": SetPreferenceArgs,
            "reject_current_product": RejectCurrentProductArgs,
        }
        try:
            argument_types[proposal.tool].model_validate(proposal.args)
        except ValidationError:
            return PolicyResult(action="rejected", reason="Invalid tool arguments")
        if not explicitly_requested(proposal, context.get("user_text", "")):
            return PolicyResult(
                action="clarification",
                clarification_text="Please explicitly confirm the exact change and value you want to set.",
            )

        if proposal.tool == "create_price_alert":
            try:
                alert_args = CreatePriceAlertArgs.model_validate(proposal.args)
            except ValidationError:
                return PolicyResult(action="rejected", reason="Invalid alert arguments")

            target = alert_args.target_price
            if target <= 0:
                return PolicyResult(action="rejected", reason="Target price must be greater than 0")

            current_price = context.get("current_price")
            if current_price is not None:
                current_dec = decimal.Decimal(str(current_price))
                if target >= current_dec:
                    return PolicyResult(
                        action="rejected",
                        reason=f"Target price ({target}) must be below the current price ({current_dec})",
                    )

            # Check user active alert limit
            active_alerts = self.alert_repo.list_alerts(db, user_id=user.id, status="active")
            if len(active_alerts) >= settings.MAX_ACTIVE_ALERTS_PER_USER:  # PLACEHOLDER: 20
                return PolicyResult(
                    action="rejected",
                    reason=f"Active alert limit ({settings.MAX_ACTIVE_ALERTS_PER_USER}) reached for this user",
                )

            # Check if active alert already exists on this listing
            listing_id_raw = context.get("listing_id")
            if listing_id_raw:
                listing_id = (
                    listing_id_raw
                    if isinstance(listing_id_raw, uuid.UUID)
                    else uuid.UUID(str(listing_id_raw))
                )
                existing = self.alert_repo.get_active_alert_by_user_listing(
                    db,
                    user_id=user.id,
                    listing_id=listing_id,
                )
                if existing:
                    return PolicyResult(
                        action="clarification",
                        clarification_text=(
                            f"You already have an active alert for this product at ₹{existing.target_price}. "
                            f"Would you like to update it to ₹{target} instead?"
                        ),
                    )

            return PolicyResult(action="allowed")

        if proposal.tool == "set_preference":
            val = proposal.args.get("value")
            if isinstance(val, str) and len(val) > settings.MAX_PREFERENCE_STRING_LENGTH:
                return PolicyResult(action="rejected", reason="Preference string value is too long")
            if isinstance(val, int) and (val < 0 or val > settings.MAX_PREFERENCE_INT_VALUE):
                return PolicyResult(
                    action="rejected", reason="Preference integer value is out of bounds"
                )

            if context.get("context_stale_version"):
                return PolicyResult(
                    action="clarification",
                    clarification_text="The shopping context was updated recently. Would you like to confirm setting this preference?",
                )
            return PolicyResult(action="allowed")

        if proposal.tool == "set_budget":
            try:
                budget_args = SetBudgetArgs.model_validate(proposal.args)
            except ValidationError:
                return PolicyResult(action="rejected", reason="Invalid budget arguments")

            if budget_args.budget_max <= 0:
                return PolicyResult(action="rejected", reason="Budget must be greater than 0")

            if context.get("context_stale_version"):
                return PolicyResult(
                    action="clarification",
                    clarification_text=f"The shopping context was updated recently. Would you like to set your budget to ₹{budget_args.budget_max}?",
                )
            return PolicyResult(action="allowed")

        if proposal.tool == "reject_current_product":
            listing_id_raw = context.get("listing_id")
            if not listing_id_raw:
                return PolicyResult(
                    action="rejected",
                    reason="No current product snapshot bound to this message to reject",
                )
            return PolicyResult(action="allowed")

        return PolicyResult(action="rejected", reason="Tool policy evaluation failed")
