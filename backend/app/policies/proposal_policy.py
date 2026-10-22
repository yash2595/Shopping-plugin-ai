import decimal
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
from sqlalchemy.orm import Session

from app.ai.contracts import PREFERENCE_KEY_WHITELIST, CreatePriceAlertArgs, ToolProposal
from app.config import get_settings
from app.models.user import User
from app.repositories.alert_repo import AlertRepository

settings = get_settings()


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

        if proposal.tool == "create_price_alert":
            try:
                alert_args = CreatePriceAlertArgs.model_validate(proposal.args)
            except Exception as e:
                return PolicyResult(action="rejected", reason=f"Invalid alert arguments: {str(e)}")

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
            key = proposal.args.get("key")
            if not key or key not in PREFERENCE_KEY_WHITELIST:
                return PolicyResult(
                    action="rejected",
                    reason=f"Preference key '{key}' is not allowed. Whitelist: {sorted(PREFERENCE_KEY_WHITELIST)}",
                )
            val = proposal.args.get("value")
            if isinstance(val, str) and len(val) > settings.MAX_PREFERENCE_STRING_LENGTH:
                return PolicyResult(action="rejected", reason="Preference string value is too long")
            if isinstance(val, int) and (val < 0 or val > settings.MAX_PREFERENCE_INT_VALUE):
                return PolicyResult(action="rejected", reason="Preference integer value is out of bounds")

            if context.get("context_stale_version"):
                return PolicyResult(
                    action="clarification",
                    clarification_text="The shopping context was updated recently. Would you like to confirm setting this preference?",
                )
            return PolicyResult(action="allowed")

        if proposal.tool == "set_budget":
            try:
                from app.ai.contracts import SetBudgetArgs
                budget_args = SetBudgetArgs.model_validate(proposal.args)
            except Exception as e:
                return PolicyResult(action="rejected", reason=f"Invalid budget arguments: {str(e)}")

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
