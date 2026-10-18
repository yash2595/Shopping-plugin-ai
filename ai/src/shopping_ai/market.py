"""Cross-store search checkpoints and evidence ranking; never operates a browser.

The authenticated caller supplies supported stores, persists state with CAS, and
executes each search in the user's visible browser. Store content is data only.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from shopping_ai.config import AIConfig
from shopping_ai.models import Contract, Identifier, Money, ShortText

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


def _normal(value: str) -> str:
    return " ".join(value.casefold().split())


class ProductIdentity(Contract):
    brand: ShortText
    model: ShortText
    # Include every relevant distinction: storage, RAM, colour, size, pack count,
    # region, etc. Caller must clarify missing required distinctions before search.
    attributes: dict[ShortText, ShortText] = Field(min_length=1, max_length=20)
    condition: Literal["new", "used", "refurbished"] = "new"

    @field_validator("attributes")
    @classmethod
    def unique_labels(cls, value):
        labels = [_normal(key) for key in value]
        if len(set(labels)) != len(labels):
            raise ValueError("Ambiguous identity attributes")
        return value

    def matches(self, other: "ProductIdentity") -> bool:
        return (
            _normal(self.brand) == _normal(other.brand)
            and _normal(self.model) == _normal(other.model)
            and self.condition == other.condition
            and {_normal(k): _normal(v) for k, v in self.attributes.items()}
            == {_normal(k): _normal(v) for k, v in other.attributes.items()}
        )


class Store(Contract):
    store_id: Identifier
    # Trusted adapter configuration, not LLM/source-provided navigation URLs.
    origin: Annotated[str, Field(max_length=200)]
    search_path: Annotated[str, Field(max_length=200, pattern=r"^/[^?#]*$")]
    query_parameter: Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")]

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Store requires a plain HTTPS origin")
        return value.rstrip("/")


class SearchTask(Contract):
    deadline_at: AwareDatetime
    task_id: Identifier
    store: Store
    url: Annotated[str, Field(max_length=3000)]
    visible: Literal[True] = True


class Offer(Contract):
    offer_id: Identifier
    snapshot_id: Identifier
    identity: ProductIdentity
    url: Annotated[str, Field(max_length=3000)]
    seller: ShortText
    observed_at: AwareDatetime
    in_stock: StrictBool | None = None
    price: Money
    currency: Currency
    # Zero means observed free/included. None means unobserved, never zero.
    shipping: Money | None = None
    extra_tax_and_fees: Money | None = None
    conditional_discount_note: ShortText | None = None

    @property
    def total(self) -> Decimal | None:
        if self.shipping is None or self.extra_tax_and_fees is None:
            return None
        return self.price + self.shipping + self.extra_tax_and_fees


class SearchReceipt(Contract):
    search_id: Identifier
    owner_id: Identifier
    session_id: Identifier
    task_id: Identifier
    delivery_context: ShortText
    status: Literal["complete", "failed", "blocked", "timed_out"]
    offers: tuple[Offer, ...] = Field(default_factory=tuple, max_length=20)

    @model_validator(mode="after")
    def coherent(self):
        if self.status != "complete" and self.offers:
            raise ValueError("Failed searches cannot supply offers")
        return self


class MarketSearch(Contract):
    search_id: Identifier
    owner_id: Identifier
    session_id: Identifier
    version: Annotated[int, Field(strict=True, ge=0)] = 0
    identity: ProductIdentity
    currency: Currency
    delivery_context: ShortText  # e.g. delivery postcode; no full address needed
    created_at: AwareDatetime
    deadline_at: AwareDatetime
    tasks: tuple[SearchTask, ...] = Field(min_length=2, max_length=6)
    receipts: tuple[SearchReceipt, ...] = Field(default_factory=tuple, max_length=6)

    @model_validator(mode="after")
    def coherent_checkpoint(self):
        if not 1 <= (self.deadline_at - self.created_at).total_seconds() <= 600:
            raise ValueError("Search deadline must be 1-600 seconds after creation")
        if any(task.deadline_at != self.deadline_at for task in self.tasks):
            raise ValueError("Task deadline must match its search checkpoint")
        tasks = {task.task_id: task for task in self.tasks}
        if len(tasks) != len(self.tasks):
            raise ValueError("Duplicate search tasks")
        if len({task.store.store_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate search stores")
        if len({task.store.origin for task in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate store origins")
        seen, offer_ids, snapshot_ids = set(), set(), set()
        for receipt in self.receipts:
            if receipt.task_id not in tasks or receipt.task_id in seen:
                raise ValueError("Invalid or duplicate checkpoint receipt")
            if (
                receipt.search_id,
                receipt.owner_id,
                receipt.session_id,
                receipt.delivery_context,
            ) != (
                self.search_id,
                self.owner_id,
                self.session_id,
                self.delivery_context,
            ):
                raise ValueError("Checkpoint receipt belongs to another search")
            seen.add(receipt.task_id)
            origin = urlsplit(tasks[receipt.task_id].store.origin)
            for offer in receipt.offers:
                url = urlsplit(offer.url)
                if (url.scheme, url.netloc) != (origin.scheme, origin.netloc) or url.fragment:
                    raise ValueError("Checkpoint offer origin mismatch")
                if offer.offer_id in offer_ids or offer.snapshot_id in snapshot_ids:
                    raise ValueError("Duplicate checkpoint offer evidence")
                offer_ids.add(offer.offer_id)
                snapshot_ids.add(offer.snapshot_id)
        return self


class RankedOffer(Contract):
    store_id: Identifier
    offer: Offer
    total: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def observed_total(self):
        if self.offer.total is None or self.total != self.offer.total:
            raise ValueError("Ranked total must equal observed price plus costs")
        return self


ExclusionReason = Literal[
    "identity_mismatch",
    "currency_mismatch",
    "out_of_stock",
    "stock_unknown",
    "future_observation",
    "predates_search",
    "stale_observation",
    "unknown_total_cost",
]


class OfferExclusion(Contract):
    store_id: Identifier
    offer_id: Identifier
    reasons: tuple[ExclusionReason, ...] = Field(min_length=1, max_length=8)


class MarketResult(Contract):
    search_id: Identifier
    status: Literal["searching", "complete", "partial"]
    ranked: tuple[RankedOffer, ...]
    excluded_offer_ids: tuple[Identifier, ...]
    exclusions: tuple[OfferExclusion, ...] = Field(default_factory=tuple, max_length=120)
    unresolved_store_ids: tuple[Identifier, ...]
    # All tied cheapest offers; empty while unfinished or any possible competitor
    # has unobserved costs. Never a global internet-wide cheapest claim.
    cheapest_offer_ids: tuple[Identifier, ...]
    message: str
    speech_text: str

    @model_validator(mode="after")
    def evidence_partition(self):
        rejected = tuple(item.offer_id for item in self.exclusions)
        ranked_ids = [item.offer.offer_id for item in self.ranked]
        if rejected != self.excluded_offer_ids or len(set(rejected)) != len(rejected):
            raise ValueError("Excluded offer IDs must match their reasons")
        if len(set(ranked_ids)) != len(ranked_ids) or set(ranked_ids) & set(rejected):
            raise ValueError("Ranked and excluded evidence must be distinct")
        if not set(self.cheapest_offer_ids) <= set(ranked_ids):
            raise ValueError("Cheapest offers must belong to ranked evidence")
        if self.status != "complete" and self.cheapest_offer_ids:
            raise ValueError("Incomplete coverage cannot have confirmed cheapest offers")
        if self.status == "complete":
            minimum = min((item.total for item in self.ranked), default=None)
            winners = tuple(item.offer.offer_id for item in self.ranked if item.total == minimum)
            if self.cheapest_offer_ids != winners:
                raise ValueError("Cheapest IDs must retain every observed minimum-price tie")
        return self


def plan_search(
    *,
    search_id: str,
    owner_id: str,
    session_id: str,
    identity: ProductIdentity,
    currency: str,
    delivery_context: str,
    stores: tuple[Store, ...],
    now: datetime,
    timeout_seconds: int = 120,
) -> MarketSearch:
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise ValueError("Search timeout must be an integer in 1-600 seconds")
    identity = ProductIdentity.model_validate(identity.model_dump())
    stores = tuple(Store.model_validate(store.model_dump()) for store in stores)
    if len({s.store_id for s in stores}) != len(stores):
        raise ValueError("Duplicate stores")
    if len({s.origin for s in stores}) != len(stores):
        raise ValueError("Duplicate store origins")
    query = " ".join((identity.brand, identity.model, *identity.attributes.values()))
    return MarketSearch(
        search_id=search_id,
        owner_id=owner_id,
        session_id=session_id,
        identity=identity,
        currency=currency,
        delivery_context=delivery_context,
        created_at=now,
        deadline_at=now + timedelta(seconds=timeout_seconds),
        tasks=tuple(
            SearchTask(
                deadline_at=now + timedelta(seconds=timeout_seconds),
                task_id=f"store:{index}",
                store=store,
                url=store.origin
                + store.search_path
                + "?"
                + urlencode({store.query_parameter: query}),
            )
            for index, store in enumerate(stores)
        ),
    )


def accept_search_receipt(
    state: MarketSearch,
    receipt: SearchReceipt,
    *,
    owner_id: str,
    session_id: str,
    expected_version: int,
    now: datetime,
) -> MarketSearch:
    state = MarketSearch.model_validate(state.model_dump())
    receipt = SearchReceipt.model_validate(receipt.model_dump())
    if (owner_id, session_id) != (state.owner_id, state.session_id):
        raise ValueError("SEARCH_OWNER_MISMATCH")
    if expected_version != state.version:
        raise ValueError("SEARCH_VERSION_CONFLICT")
    if now.tzinfo is None or now < state.created_at:
        raise ValueError("Invalid search clock")
    if now >= state.deadline_at:
        raise ValueError("SEARCH_EXPIRED")
    if receipt.status == "timed_out":
        raise ValueError("Timeout receipts are created only by expire_search")
    if (receipt.search_id, receipt.owner_id, receipt.session_id) != (
        state.search_id,
        owner_id,
        session_id,
    ):
        raise ValueError("RECEIPT_BINDING_MISMATCH")
    if receipt.delivery_context != state.delivery_context:
        raise ValueError("DELIVERY_CONTEXT_MISMATCH")
    tasks = {task.task_id: task for task in state.tasks}
    if receipt.task_id not in tasks:
        raise ValueError("UNKNOWN_SEARCH_TASK")
    if receipt.task_id in {r.task_id for r in state.receipts}:
        raise ValueError("DUPLICATE_SEARCH_RECEIPT")
    seen_offers = {o.offer_id for r in state.receipts for o in r.offers}
    seen_snapshots = {o.snapshot_id for r in state.receipts for o in r.offers}
    origin = urlsplit(tasks[receipt.task_id].store.origin)
    for offer in receipt.offers:
        if offer.observed_at > now:
            raise ValueError("FUTURE_OFFER_EVIDENCE")
        if offer.observed_at < state.created_at:
            raise ValueError("OFFER_PREDATES_SEARCH")
        url = urlsplit(offer.url)
        if (url.scheme, url.netloc) != (origin.scheme, origin.netloc) or url.fragment:
            raise ValueError("OFFER_ORIGIN_MISMATCH")
        if offer.offer_id in seen_offers or offer.snapshot_id in seen_snapshots:
            raise ValueError("DUPLICATE_OFFER_EVIDENCE")
        seen_offers.add(offer.offer_id)
        seen_snapshots.add(offer.snapshot_id)
    return MarketSearch.model_validate(
        state.model_dump()
        | {
            "version": state.version + 1,
            "receipts": (*state.receipts, receipt),
        }
    )


def expire_search(
    state: MarketSearch,
    *,
    owner_id: str,
    session_id: str,
    expected_version: int,
    now: datetime,
) -> MarketSearch:
    """Settle unreturned tasks after deadline; never retry browser work."""
    state = MarketSearch.model_validate(state.model_dump())
    if (owner_id, session_id) != (state.owner_id, state.session_id):
        raise ValueError("SEARCH_OWNER_MISMATCH")
    if expected_version != state.version:
        raise ValueError("SEARCH_VERSION_CONFLICT")
    if now.tzinfo is None or now < state.created_at:
        raise ValueError("Invalid search clock")
    if now < state.deadline_at:
        raise ValueError("SEARCH_NOT_EXPIRED")
    returned = {receipt.task_id for receipt in state.receipts}
    missing = tuple(
        SearchReceipt(
            search_id=state.search_id,
            owner_id=state.owner_id,
            session_id=state.session_id,
            task_id=task.task_id,
            delivery_context=state.delivery_context,
            status="timed_out",
        )
        for task in state.tasks
        if task.task_id not in returned
    )
    if not missing:
        return state
    return MarketSearch.model_validate(
        state.model_dump()
        | {
            "version": state.version + 1,
            "receipts": (*state.receipts, *missing),
        }
    )


def rank_offers(
    state: MarketSearch,
    *,
    now: datetime,
    config: AIConfig,
    language: Literal["en", "hi-Latn"] = "en",
) -> MarketResult:
    state = MarketSearch.model_validate(state.model_dump())
    if now.tzinfo is None or now < state.created_at:
        raise ValueError("Valid aware ranking time required")
    tasks = {task.task_id: task for task in state.tasks}
    completed = {r.task_id for r in state.receipts if r.status == "complete"}
    unresolved = tuple(t.store.store_id for t in state.tasks if t.task_id not in completed)
    ranked, excluded, exclusions = [], [], []
    uncertain = False
    for receipt in state.receipts:
        for offer in receipt.offers:
            age = (now - offer.observed_at).total_seconds()
            reasons = []
            if not state.identity.matches(offer.identity):
                reasons.append("identity_mismatch")
            if offer.currency != state.currency:
                reasons.append("currency_mismatch")
            if offer.in_stock is False:
                reasons.append("out_of_stock")
            elif offer.in_stock is None:
                reasons.append("stock_unknown")
            if age < 0:
                reasons.append("future_observation")
            elif age > config.max_snapshot_age_seconds:
                reasons.append("stale_observation")
            if offer.observed_at < state.created_at:
                reasons.append("predates_search")
            if offer.total is None:
                reasons.append("unknown_total_cost")
            if reasons:
                excluded.append(offer.offer_id)
                exclusions.append(
                    OfferExclusion(
                        store_id=tasks[receipt.task_id].store.store_id,
                        offer_id=offer.offer_id,
                        reasons=tuple(reasons),
                    )
                )
                # A mismatched variant or unavailable offer cannot win. Other
                # unknown evidence prevents a definitive cheapest claim.
                if state.identity.matches(offer.identity) and offer.in_stock is not False:
                    uncertain = True
                continue
            ranked.append(
                RankedOffer(
                    store_id=tasks[receipt.task_id].store.store_id,
                    offer=offer,
                    total=offer.total,
                )
            )
    ranked.sort(key=lambda item: (item.total, item.store_id, item.offer.offer_id))
    pending = len(state.receipts) < len(state.tasks) and now < state.deadline_at
    status = "searching" if pending else "partial" if unresolved or uncertain else "complete"
    cheapest = (
        tuple(item.offer.offer_id for item in ranked if item.total == ranked[0].total)
        if ranked and status == "complete"
        else ()
    )
    hi = language == "hi-Latn"
    if cheapest:
        # At most six distinct configured stores, even with many tied offers.
        names = ", ".join(
            dict.fromkeys(item.store_id for item in ranked if item.offer.offer_id in cheapest)
        )
        message = (
            f"Checked stores mein lowest verified total: {state.currency} {ranked[0].total:.2f}. "
            f"Stores: {names}."
            if hi
            else f"Checked stores: lowest verified total {state.currency} {ranked[0].total:.2f}. "
            f"Stores: {names}."
        )
        if len(cheapest) > 1:
            message += (
                f" {len(cheapest)} offers ka lowest total same hai."
                if hi
                else f" {len(cheapest)} offers share this lowest total."
            )
    elif ranked:
        message = (
            "Search incomplete hai: verified totals dikha raha hoon; cheapest confirm nahi hai."
            if hi
            else "Search incomplete: showing verified totals; cheapest offer is not confirmed."
        )
    else:
        message = (
            "Fresh exact-match offers ke verified total costs available nahi hain."
            if hi
            else "No fresh, exact-match offers with verified total costs are available."
        )
    disclaimer = (
        " Conditional discounts deduct nahi kiye. Prices diye gaye delivery context ke liye hain."
        if hi
        else " Conditional discounts are not deducted. Prices use the supplied delivery context."
    )
    speech = message + disclaimer
    if unresolved:
        speech += (
            f" {len(tasks)} stores mein se {len(unresolved)} ke complete results nahi mile."
            if hi
            else f" Of {len(tasks)} requested stores, {len(unresolved)} lack complete results."
        )
    message += disclaimer
    if unresolved:
        message += (
            (
                " In stores ke complete results nahi mile: "
                if hi
                else " Stores without complete results: "
            )
            + ", ".join(unresolved)
            + "."
        )
    for item in ranked[:5]:
        message += (
            f"\n{item.store_id}: {item.offer.currency} {item.total:.2f} "
            f"(price {item.offer.price:.2f} + delivery {item.offer.shipping:.2f} "
            f"+ extra tax/fees {item.offer.extra_tax_and_fees:.2f})."
        )
    if excluded:
        message += (
            f" {len(excluded)} offers exclude hue; reasons structured results mein hain."
            if hi
            else f" {len(excluded)} offers excluded; see structured evidence reasons."
        )
    if len(ranked) > 5:
        message += (
            " Baaki offers structured results mein hain."
            if hi
            else " Additional offers are available in the structured results."
        )
    return MarketResult(
        search_id=state.search_id,
        status=status,
        ranked=tuple(ranked),
        excluded_offer_ids=tuple(excluded),
        exclusions=tuple(exclusions),
        unresolved_store_ids=unresolved,
        cheapest_offer_ids=cheapest,
        message=message,
        speech_text=speech,
    )
