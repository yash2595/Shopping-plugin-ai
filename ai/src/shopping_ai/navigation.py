"""Read-only offer navigation proposals and trusted visible-browser receipts."""

import ipaddress
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
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


# Browser-goal planning is separate from product decisions and offer verification.
# These values are proposals only. The native browser remains the executor.


class BrowserPlanStep(Contract):
    kind: Literal[
        "navigate",
        "search",
        "back",
        "forward",
        "reload",
        "scroll_down",
        "scroll_up",
        "open_result",
        "switch",
    ]
    url: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    query: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    site: Literal["amazon", "flipkart", "google"] | None = None
    index: Annotated[int, Field(strict=True, ge=1, le=5)] | None = None
    tab_id: Identifier | None = None

    @model_validator(mode="after")
    def correct_arguments(self):
        actual = {
            key
            for key in ("url", "query", "site", "index", "tab_id")
            if getattr(self, key) is not None
        }
        expected = {
            "navigate": {"url"},
            "search": {"query", "site"},
            "open_result": {"index"},
            "switch": {"tab_id"},
        }.get(self.kind, set())
        if actual != expected:
            raise ValueError("Unexpected navigation arguments")
        return self


class BrowserPlanRequest(Contract):
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    current_site: Literal["amazon", "flipkart", "google"] | None = None
    allowed_tab_ids: tuple[Identifier, ...] = Field(default=(), max_length=20)


class BrowserModelPlan(Contract):
    status: Literal["ready", "needs_input", "not_navigation"]
    steps: tuple[BrowserPlanStep, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def executable_only_when_ready(self):
        if (self.status == "ready") != bool(self.steps):
            raise ValueError("Ready plans require steps; other statuses cannot execute")
        return self


class BrowserGoalPlan(BrowserModelPlan):
    plan_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    processing: Literal["rules", "model"] = "rules"
    inference_fallback: Literal["deterministic", "cloud"] | None = None


BROWSER_SITES = {
    "amazon": "https://www.amazon.in/",
    "flipkart": "https://www.flipkart.com/",
    "google": "https://www.google.com/",
}
BROWSER_SEARCH = {
    "amazon": "https://www.amazon.in/s?k=",
    "flipkart": "https://www.flipkart.com/search?q=",
    "google": "https://www.google.com/search?q=",
}


def normalise_browser_message(message: str) -> str:
    text = (
        message.strip()
        .lower()
        .replace("अमेज़न", "amazon")
        .replace("अमेजन", "amazon")
        .replace("फ्लिपकार्ट", "flipkart")
        .replace("गूगल", "google")
    )
    return re.sub(r"^(please|bhai|saarthi)[, ]+", "", text).strip()


def public_browser_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.port is not None
            or "." not in url.hostname
            or any(c.isspace() or ord(c) < 32 or c == chr(92) for c in value)
        ):
            return False
        if url.hostname.rstrip(".") in {
            "localhost",
            "metadata.google.internal",
        } or url.hostname.rstrip(".").endswith((".localhost", ".local", ".internal")):
            return False
        try:
            if not ipaddress.ip_address(url.hostname.rstrip(".")).is_global:
                return False
        except ValueError:
            if re.fullmatch(
                r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*",
                url.hostname,
                re.I,
            ):
                return False
        return (
            re.search(
                r"/(cart|checkout|payment|buy|order|logout|delete)(?:[/?-]|$)", url.path, re.I
            )
            is None
        )
    except ValueError:
        return False


def browser_goal_is_unsafe(message: str) -> bool:
    text = normalise_browser_message(message)
    return bool(
        re.search(
            r"\b(buy|purchase|checkout|payment|pay|login|delete|add to cart|do not|dont|"
            r"don't|mat|nahi)\b|नहीं|मत|खरीद",
            text,
        )
    )


def validate_browser_plan(
    request: BrowserPlanRequest, candidate: BrowserModelPlan, *, processing="rules"
) -> BrowserGoalPlan:
    if candidate.status != "ready":
        return BrowserGoalPlan(status=candidate.status, processing=processing)
    if browser_goal_is_unsafe(request.message) or re.match(
        r"^(how|why|what|kaise|kyun)\b", normalise_browser_message(request.message)
    ):
        return BrowserGoalPlan(status="needs_input", processing=processing)
    text = normalise_browser_message(request.message)
    site_text = re.sub(r"https://[^\s]+", " ", text)
    named = {name for name in BROWSER_SITES if re.search(rf"\b{name}\b", site_text)}
    permitted_sites = named or ({request.current_site} if request.current_site else set())
    literals = {match.rstrip(".,!?") for match in re.findall(r"https://[^\s]+", request.message)}
    output = []
    cues = {
        "open_result": r"\b(result|product|pehla|first|offer)\b|पहला|रिजल्ट",
        "scroll_down": r"\b(scroll|neeche)\b|नीचे",
        "scroll_up": r"\b(scroll|upar)\b|ऊपर",
        "back": r"\b(back|peeche|pichhe)\b|पीछे",
        "forward": r"\b(forward|aage)\b|आगे",
        "reload": r"\b(reload|refresh)\b|रीफ्रेश",
        "switch": r"\btab\b|टैब",
    }
    for step in candidate.steps:
        if step.kind in cues and not re.search(cues[step.kind], text):
            return BrowserGoalPlan(status="needs_input", processing=processing)
        if step.kind == "navigate":
            if not public_browser_url(step.url) or not (
                step.url in literals
                or step.url in {BROWSER_SITES[name] for name in permitted_sites}
            ):
                return BrowserGoalPlan(status="needs_input", processing=processing)
        elif step.kind == "search":
            # The planner may select/reorder user words, never invent a search goal.
            words = re.findall(r"\w+", step.query.lower())
            if (
                step.site not in permitted_sites
                or not words
                or any(word not in re.findall(r"\w+", text) for word in words)
            ):
                return BrowserGoalPlan(status="needs_input", processing=processing)
            step = BrowserPlanStep(
                kind="navigate", url=BROWSER_SEARCH[step.site] + quote(step.query, safe="")
            )
        elif step.kind == "switch" and step.tab_id not in request.allowed_tab_ids:
            return BrowserGoalPlan(status="needs_input", processing=processing)
        output.append(step)
    return BrowserGoalPlan(status="ready", steps=tuple(output), processing=processing)


def plan_browser_goal(request: BrowserPlanRequest) -> BrowserGoalPlan:
    text = normalise_browser_message(request.message)
    if browser_goal_is_unsafe(text):
        return BrowserGoalPlan(status="needs_input")
    pieces = re.split(
        r"\s*(?:;|\band then\b|\bthen\b|\baur phir\b|\bphir\b|और फिर|\s+और\s+|\s+aur\s+)\s*", text
    )
    if len(pieces) > 8 or any(not item for item in pieces):
        return BrowserGoalPlan(status="needs_input")
    steps = []
    site = request.current_site
    for piece in pieces:
        direct = {
            "back": "back",
            "go back": "back",
            "peeche jao": "back",
            "forward": "forward",
            "go forward": "forward",
            "refresh": "reload",
            "reload": "reload",
            "scroll down": "scroll_down",
            "neeche jao": "scroll_down",
            "scroll up": "scroll_up",
            "upar jao": "scroll_up",
        }
        if piece in direct:
            steps.append(BrowserPlanStep(kind=direct[piece]))
            continue
        if re.fullmatch(r"open first result|pehla result kholo|पहला रिजल्ट खोलो", piece):
            steps.append(BrowserPlanStep(kind="open_result", index=1))
            continue
        result = re.fullmatch(r"(?:open result|result) ([1-5])(?: kholo)?", piece)
        if result:
            steps.append(BrowserPlanStep(kind="open_result", index=int(result[1])))
            continue
        opened = re.fullmatch(
            r"(?:open|visit|go to|navigate to) (.+)|(.+?) (?:kholo|khol do|खोलो)", piece
        )
        if opened:
            value = opened[1] or opened[2]
            url = BROWSER_SITES.get(
                value, value if value.startswith("https://") else "https://" + value
            )
            if len(url) > 2000:
                return BrowserGoalPlan(status="needs_input")
            steps.append(BrowserPlanStep(kind="navigate", url=url))
            site = next((name for name, address in BROWSER_SITES.items() if url == address), site)
            continue
        found = re.fullmatch(
            r"(?:search|find|dhundo|dhoondo) (.+?)(?: (?:on|par|pe) (amazon|flipkart|google))?",
            piece,
        )
        reverse = re.fullmatch(
            r"(?:mujhe )?(amazon|flipkart|google) (?:pe|par|पर) (.+?) "
            r"(?:search karo|dhundo|dhoondo|dikhado|dikhao|सर्च करो|खोजो)",
            piece,
        )
        if found or reverse:
            query = found[1] if found else reverse[2]
            selected = (found[2] or site) if found else reverse[1]
            if not selected or len(query) > 200:
                return BrowserGoalPlan(status="needs_input")
            steps.append(BrowserPlanStep(kind="search", query=query, site=selected))
            site = selected
            continue
        return BrowserGoalPlan(
            status="needs_input"
            if steps
            or len(pieces) > 1
            or re.search(
                r"\b(open|navigate|search|kholo|dhoondo|website|browser|amazon|flipkart|google|dikhao|dikhado)\b",
                text,
            )
            else "not_navigation"
        )
    return validate_browser_plan(request, BrowserModelPlan(status="ready", steps=tuple(steps)))


class RulesBrowserPlanner:
    async def plan_navigation(self, request: BrowserPlanRequest) -> BrowserModelPlan:
        plan = plan_browser_goal(request)
        return BrowserModelPlan(status=plan.status, steps=plan.steps)
