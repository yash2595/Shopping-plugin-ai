"""Read-only offer navigation proposals and trusted visible-browser receipts."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from shopping_ai.browser import BrowserTarget
from shopping_ai.config import AIConfig
from shopping_ai.market import MarketSearch, Offer, ProductIdentity, rank_offers
from shopping_ai.models import Contract, Identifier, ProductSnapshot
from shopping_ai.orchestrator import Principal


class OfferNavigation(Contract):
    action_id: Identifier
    search_id: Identifier
    owner_id: Identifier
    session_id: Identifier
    offer: Offer
    source: BrowserTarget
    created_at: AwareDatetime
    expires_at: AwareDatetime
    reuse_visible_tab: Literal[True] = True

    @model_validator(mode="after")
    def coherent(self):
        if (
            not self.source.visible
            or not 0 < (self.expires_at - self.created_at).total_seconds() <= 120
        ):
            raise ValueError("Navigation requires a visible source and bounded deadline")
        return self


class NavigationReceipt(Contract):
    action_id: Identifier
    search_id: Identifier
    owner_id: Identifier
    session_id: Identifier
    status: Literal["opened", "failed"]
    observed_at: AwareDatetime
    url: Annotated[str, Field(max_length=3000)] | None = None
    target: BrowserTarget | None = None
    identity: ProductIdentity | None = None
    product: ProductSnapshot | None = None

    @model_validator(mode="after")
    def evidence(self):
        fields = (self.url, self.target, self.identity, self.product)
        if self.status == "opened" and any(value is None for value in fields):
            raise ValueError("Opened navigation requires actual page evidence")
        if self.status == "failed" and any(value is not None for value in fields):
            raise ValueError("Failed navigation cannot contain a verified product")
        return self


class OfferPageVerification(Contract):
    """Grounded listing-to-page comparison, created only after receipt verification."""

    proposal: OfferNavigation
    product: ProductSnapshot

    @property
    def changed(self) -> bool:
        old = self.proposal.offer
        return (old.price, old.currency, old.in_stock) != (
            self.product.price,
            self.product.currency,
            self.product.in_stock,
        )

    @property
    def base_price_difference(self) -> Decimal | None:
        if self.product.price is None or self.product.currency != self.proposal.offer.currency:
            return None
        return self.product.price - self.proposal.offer.price

    def explanation(self, language: Literal["en", "hi-Latn"]) -> str:
        old, new = self.proposal.offer, self.product
        hi = language == "hi-Latn"
        rows = []
        if (old.price, old.currency) != (new.price, new.currency):
            current = "unknown" if new.price is None else f"{new.currency} {new.price:.2f}"
            rows.append(
                ("Base price pehle " if hi else "Previous base price ")
                + f"{old.currency} {old.price:.2f}; "
                + ("ab " if hi else "current ")
                + current
                + "."
            )
            delta = self.base_price_difference
            if delta is not None:
                direction = (
                    ("badha" if delta > 0 else "ghata")
                    if hi
                    else ("increased" if delta > 0 else "decreased")
                )
                rows.append(f"Base price {direction}: {old.currency} {abs(delta):.2f}.")
        if old.in_stock != new.in_stock:
            availability = {True: "in stock", False: "out of stock", None: "unknown"}
            rows.append(
                f"Stock: previous {availability[old.in_stock]}; "
                f"current {availability[new.in_stock]}."
            )
        if rows:
            rows.append(
                "Fresh delivered total verify nahi hua; cross-store prices refresh karo."
                if hi
                else "A fresh delivered total is not verified; refresh cross-store prices."
            )
        return " ".join(rows)


def propose_navigation(
    market: MarketSearch,
    *,
    principal: Principal,
    target: BrowserTarget,
    offer_id: str,
    now: datetime,
    config: AIConfig,
) -> OfferNavigation:
    market = MarketSearch.model_validate(market.model_dump())
    target = BrowserTarget.model_validate(target.model_dump())
    if (market.owner_id, market.session_id) != (principal.owner_id, principal.session_id):
        raise ValueError("NAVIGATION_OWNER_MISMATCH")
    if len(market.receipts) < len(market.tasks):
        raise ValueError("SEARCH_STILL_PENDING")
    result = rank_offers(market, now=now, config=config)
    offer = next((item.offer for item in result.ranked if item.offer.offer_id == offer_id), None)
    if offer is None:
        raise ValueError("NO_FRESH_VERIFIED_OFFER")
    if not target.visible:
        raise ValueError("VISIBLE_TAB_REQUIRED")
    remaining = config.max_snapshot_age_seconds - (now - offer.observed_at).total_seconds()
    if remaining <= 0:
        raise ValueError("NO_FRESH_VERIFIED_OFFER")
    return OfferNavigation(
        action_id=uuid4().hex,
        search_id=market.search_id,
        owner_id=principal.owner_id,
        session_id=principal.session_id,
        offer=offer,
        source=target,
        created_at=now,
        expires_at=now + timedelta(seconds=min(config.timeout_seconds, remaining)),
    )


def verify_navigation(
    action: OfferNavigation,
    receipt: NavigationReceipt,
    *,
    principal: Principal,
    now: datetime,
    config: AIConfig,
) -> ProductSnapshot | None:
    action = OfferNavigation.model_validate(action.model_dump())
    receipt = NavigationReceipt.model_validate(receipt.model_dump())
    if (action.owner_id, action.session_id) != (principal.owner_id, principal.session_id):
        raise ValueError("NAVIGATION_OWNER_MISMATCH")
    if (receipt.action_id, receipt.search_id, receipt.owner_id, receipt.session_id) != (
        action.action_id,
        action.search_id,
        principal.owner_id,
        principal.session_id,
    ):
        raise ValueError("NAVIGATION_RECEIPT_MISMATCH")
    if now.tzinfo is None or now < action.created_at or now >= action.expires_at:
        raise ValueError("NAVIGATION_EXPIRED")
    if not action.created_at <= receipt.observed_at <= now:
        raise ValueError("INVALID_NAVIGATION_TIME")
    if receipt.status == "failed":
        return None
    if (
        receipt.url != action.offer.url
        or receipt.target.tab_id != action.source.tab_id
        or not receipt.target.visible
        or receipt.target.document_id != receipt.product.document_id
        or receipt.product.observed_at != receipt.observed_at
        or not 0 <= (now - receipt.observed_at).total_seconds() <= config.max_snapshot_age_seconds
        or not action.offer.identity.matches(receipt.identity)
    ):
        raise ValueError("NAVIGATION_EVIDENCE_MISMATCH")
    return receipt.product
