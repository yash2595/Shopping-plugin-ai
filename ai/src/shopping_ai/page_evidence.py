"""Bounded JSON-LD/visible-page agreement; no network, navigation or model calls."""

import json
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Annotated, Literal
from urllib.parse import urldefrag, urlsplit

from pydantic import AwareDatetime, Field, model_validator

from shopping_ai.browser import BrowserResult, BrowserTarget, CartSnapshot
from shopping_ai.config import AIConfig
from shopping_ai.market import MarketSearch, Offer, ProductIdentity
from shopping_ai.models import Contract, Identifier, Money, ProductSnapshot, ShortText, Variant
from shopping_ai.navigation import NavigationReceipt, OfferNavigation
from shopping_ai.orchestrator import Principal, RunState


class PageEvidenceError(ValueError):
    """Stable reason code only; never echo raw page text in errors."""


MAX_CAPTURE_BYTES = 8_000_000


MAX_CART_CAPTURE_BYTES = 262_144


class CapturedCart(Contract):
    """Trusted adapter coverage declaration; never inferred from an empty line list."""

    target: BrowserTarget
    cart: CartSnapshot
    coverage: Literal["complete", "partial", "unknown"]

    @model_validator(mode="after")
    def coherent(self):
        if self.cart.document_id != self.target.document_id:
            raise ValueError("Cart and target documents must match")
        return self


class ObservedVariantControl(Contract):
    """Trusted adapter observation of one full-variant live select control."""

    control_id: Identifier
    document_id: Identifier
    observed_at: AwareDatetime
    variants: tuple[Variant, ...] = Field(min_length=1, max_length=50)
    selected_variant_id: Identifier | None = None
    price_variant_id: Identifier | None = None

    @model_validator(mode="after")
    def coherent(self):
        ids = [variant.variant_id for variant in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate observed variant IDs")
        if self.selected_variant_id is not None and self.selected_variant_id not in ids:
            raise ValueError("Selected variant must be observed")
        if self.price_variant_id is not None and (
            self.price_variant_id != self.selected_variant_id
        ):
            raise ValueError("Price must bind to the observed selection")
        return self


class CapturedPage(Contract):
    target: BrowserTarget
    url: Annotated[str, Field(min_length=1, max_length=3000)]
    observed_at: AwareDatetime
    html: str = Field(min_length=1, max_length=1_000_000, repr=False, exclude=True)
    visible_title: ShortText
    visible_price: Money | None = None
    visible_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    visible_stock: bool | None = Field(default=None, strict=True)
    variant_control: ObservedVariantControl | None = None

    @model_validator(mode="after")
    def coherent(self):
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Captured page requires an absolute HTTPS URL")
        if (self.visible_price is None) != (self.visible_currency is None):
            raise ValueError("Visible price and currency must be paired")
        if not self.target.visible:
            raise ValueError("Captured page requires a visible target")
        return self


class ObservedOfferDetails(Contract):
    """Independent executor observations, not values copied from requested identity."""

    document_id: Identifier
    observed_at: AwareDatetime
    identity: ProductIdentity
    seller: ShortText
    delivery_context: ShortText
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    shipping: Money | None = None
    extra_tax_and_fees: Money | None = None
    conditional_discount_note: ShortText | None = None


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            if len(attributes) != len(attrs):
                raise PageEvidenceError("AMBIGUOUS_SCRIPT_ATTRIBUTES")
            if (
                attributes.get("type", "").casefold().split(";", 1)[0].strip()
                == "application/ld+json"
            ):
                if len(self.blocks) >= 16:
                    raise PageEvidenceError("TOO_MANY_JSONLD_BLOCKS")
                if "src" in attributes:
                    raise PageEvidenceError("EXTERNAL_JSONLD_UNSUPPORTED")
                self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            block = "".join(self.current)
            if len(block) > 262144:
                raise PageEvidenceError("JSONLD_BLOCK_TOO_LARGE")
            self.blocks.append(block)
            self.current = None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PageEvidenceError("DUPLICATE_JSONLD_KEY")
        result[key] = value
    return result


def _constant(value):
    raise PageEvidenceError("NONFINITE_JSONLD_NUMBER")


def decode_captured_page(payload: bytes) -> CapturedPage:
    """Decode a bounded local/transport record without echoing private capture data."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_CAPTURE_BYTES:
        raise PageEvidenceError("INVALID_CAPTURE_PAYLOAD")
    try:
        data = json.loads(
            payload, object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=_constant
        )
        return CapturedPage.model_validate(data)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PageEvidenceError("INVALID_CAPTURE_PAYLOAD") from None


def decode_captured_cart(payload: bytes) -> CapturedCart:
    """Bounded strict transport decoding; no private cart content in errors."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_CART_CAPTURE_BYTES:
        raise PageEvidenceError("INVALID_CART_CAPTURE_PAYLOAD")
    try:
        data = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant)
        return CapturedCart.model_validate(data)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PageEvidenceError("INVALID_CART_CAPTURE_PAYLOAD") from None


def _has_type(node, expected):
    value = node.get("@type")
    values = value if isinstance(value, list) else [value]
    return any(
        item in (expected, f"https://schema.org/{expected}", f"http://schema.org/{expected}")
        for item in values
    )


def _product_offer(page: CapturedPage):
    parser = _Scripts()
    parser.feed(page.html)
    parser.close()
    if parser.current is not None:
        raise PageEvidenceError("INCOMPLETE_JSONLD_SCRIPT")
    products = []
    for block in parser.blocks:
        data = json.loads(
            block, object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=_constant
        )
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            candidates = (
                [node, *node.get("@graph", [])]
                if isinstance(node.get("@graph", []), list)
                else [node]
            )
            products.extend(
                item for item in candidates if isinstance(item, dict) and _has_type(item, "Product")
            )
    if len(products) != 1:
        raise PageEvidenceError("SINGLE_PRODUCT_REQUIRED")
    product = products[0]
    title = product.get("name")
    if not isinstance(title, str) or " ".join(title.casefold().split()) != " ".join(
        page.visible_title.casefold().split()
    ):
        raise PageEvidenceError("VISIBLE_TITLE_MISMATCH")
    source_url = product.get("url", product.get("@id"))
    if not isinstance(source_url, str) or urldefrag(source_url)[0] != urldefrag(page.url)[0]:
        raise PageEvidenceError("PRODUCT_URL_MISMATCH")
    offers = product.get("offers")
    if isinstance(offers, list) and len(offers) == 1:
        offers = offers[0]
    if (
        not isinstance(offers, dict)
        or not _has_type(offers, "Offer")
        or _has_type(offers, "AggregateOffer")
    ):
        raise PageEvidenceError("SINGLE_OFFER_REQUIRED")
    if "url" in offers:
        if (
            not isinstance(offers["url"], str)
            or urldefrag(offers["url"])[0] != urldefrag(page.url)[0]
        ):
            raise PageEvidenceError("OFFER_URL_MISMATCH")
    return product, offers


class _VariantSelect(HTMLParser):
    """Conservative subset: explicit closing tags, one enabled full-SKU select."""

    def __init__(self, control_id):
        super().__init__(convert_charrefs=True)
        self.control_id = control_id
        self.matches = 0
        self.inside = False
        self.option = None
        self.options = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "select" and attributes.get("id") == self.control_id:
            self.matches += 1
            if (
                self.inside
                or len(attributes) != len(attrs)
                or (
                    attributes.get("name") != "id"
                    or "disabled" in attributes
                    or "multiple" in attributes
                )
            ):
                raise PageEvidenceError("UNSUPPORTED_VARIANT_CONTROL")
            self.inside = True
        elif self.inside:
            if tag != "option" or self.option is not None or len(attributes) != len(attrs):
                raise PageEvidenceError("UNSUPPORTED_VARIANT_CONTROL")
            if "disabled" in attributes or not attributes.get("value"):
                raise PageEvidenceError("UNSUPPORTED_VARIANT_CONTROL")
            self.option = (attributes["value"], [])

    def handle_data(self, data):
        if self.option is not None:
            self.option[1].append(data)

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag == "option" and self.option is not None:
            self.options.append((self.option[0], " ".join("".join(self.option[1]).split())))
            self.option = None
        elif tag == "select" and self.option is None:
            self.inside = False
        else:
            raise PageEvidenceError("UNSUPPORTED_VARIANT_CONTROL")


def _variant_evidence(page):
    control = page.variant_control
    if control is None:
        return [], None
    if control.document_id != page.target.document_id or control.observed_at != page.observed_at:
        raise PageEvidenceError("VARIANT_CAPTURE_MISMATCH")
    if page.visible_price is not None and (
        control.selected_variant_id is None
        or control.price_variant_id != control.selected_variant_id
    ):
        raise PageEvidenceError("VARIANT_PRICE_BINDING_REQUIRED")
    parser = _VariantSelect(control.control_id)
    parser.feed(page.html)
    parser.close()
    expected = [
        (variant.variant_id, " ".join(variant.label.split())) for variant in control.variants
    ]
    if (
        parser.matches != 1
        or parser.inside
        or parser.option is not None
        or parser.options != expected
    ):
        raise PageEvidenceError("VARIANT_OPTIONS_MISMATCH")
    return list(control.variants), control.selected_variant_id


def extract_product_snapshot(
    page: CapturedPage,
    *,
    snapshot_id: str,
    product_id: str,
    now: datetime,
    config: AIConfig | None = None,
) -> ProductSnapshot:
    """Caller supplies trusted capture metadata and DOM-visible fields, never an LLM.

    HTML/JSON-LD is untrusted retailer data. This establishes agreement, not seller
    truth, exact variant identity, checkout totals or authorization to mutate.
    """
    try:
        page = CapturedPage.model_validate(page.model_dump() | {"html": page.html})
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_PAGE_CAPTURE") from None
    config = config or AIConfig()
    if (
        now.tzinfo is None
        or not 0 <= (now - page.observed_at).total_seconds() <= config.max_snapshot_age_seconds
    ):
        raise PageEvidenceError("STALE_PAGE_CAPTURE")
    try:
        _, offers = _product_offer(page)
        variants, selected_variant_id = _variant_evidence(page)
        price, currency = None, None
        if page.visible_price is not None:
            raw_price = offers.get("price")
            if isinstance(raw_price, bool) or not isinstance(raw_price, (str, int, Decimal)):
                raise PageEvidenceError("PRICE_AGREEMENT_REQUIRED")
            if (
                Decimal(raw_price) != page.visible_price
                or offers.get("priceCurrency") != page.visible_currency
            ):
                raise PageEvidenceError("VISIBLE_PRICE_MISMATCH")
            price, currency = page.visible_price, page.visible_currency
        availability = offers.get("availability")
        stock = (
            {
                "https://schema.org/InStock": True,
                "http://schema.org/InStock": True,
                "https://schema.org/OutOfStock": False,
                "http://schema.org/OutOfStock": False,
                "https://schema.org/SoldOut": False,
                "http://schema.org/SoldOut": False,
            }.get(availability)
            if isinstance(availability, str)
            else None
        )
        if stock is not None and page.visible_stock is not None and stock != page.visible_stock:
            raise PageEvidenceError("VISIBLE_STOCK_MISMATCH")
        return ProductSnapshot(
            snapshot_id=snapshot_id,
            product_id=product_id,
            document_id=page.target.document_id,
            observed_at=page.observed_at,
            title=page.visible_title,
            variants=variants,
            selected_variant_id=selected_variant_id,
            price=price,
            currency=currency,
            in_stock=stock if page.visible_stock is not None else None,
        )
    except PageEvidenceError:
        raise
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        raise PageEvidenceError("INVALID_PAGE_EVIDENCE") from None


def build_product_read_result(
    run: RunState,
    principal: Principal,
    page: CapturedPage,
    *,
    expected_run_version: int,
    snapshot_id: str,
    product_id: str,
    now: datetime,
    config: AIConfig | None = None,
) -> BrowserResult:
    """Bind trusted capture to one pending read; never create mutation receipts.

    Caller loads current authenticated state and supplies real executor metadata.
    This helper does not authenticate captures, execute reads, advance state or
    prove database persistence. Submit its result through the normal runtime.
    """
    try:
        run = RunState.model_validate(run.model_dump(warnings=False))
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_READ_CHECKPOINT") from None
    if run.principal != principal:
        raise PageEvidenceError("READ_ACCESS_DENIED")
    if run.version != expected_run_version:
        raise PageEvidenceError("STALE_READ_VERSION")
    action = run.pending
    if action is None or action.tool.name != "read_product":
        raise PageEvidenceError("PENDING_PRODUCT_READ_REQUIRED")
    try:
        page = CapturedPage.model_validate(page.model_dump() | {"html": page.html})
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_PAGE_CAPTURE") from None
    if (page.target.tab_id, page.target.document_id) != (
        action.target.tab_id,
        action.target.document_id,
    ):
        raise PageEvidenceError("READ_TARGET_MISMATCH")
    if now.tzinfo is None or not action.issued_at <= page.observed_at <= now < action.expires_at:
        raise PageEvidenceError("READ_CAPTURE_OUTSIDE_ACTION_WINDOW")
    product = extract_product_snapshot(
        page,
        snapshot_id=snapshot_id,
        product_id=product_id,
        now=now,
        config=config,
    )
    return BrowserResult(
        action_id=action.action_id,
        run_id=run.run_id,
        session_id=principal.session_id,
        run_version=action.run_version,
        source_document_id=action.target.document_id,
        target=page.target,
        observed_at=page.observed_at,
        outcome="observed",
        product=product,
    )


def build_cart_read_result(
    run: RunState,
    principal: Principal,
    cart: CartSnapshot,
    target: BrowserTarget,
    *,
    coverage: Literal["complete", "partial", "unknown"],
    expected_run_version: int,
    now: datetime,
    config: AIConfig | None = None,
) -> BrowserResult:
    """Bind trusted complete cart observations to a pending read, never a mutation.

    The executor must independently establish full cart coverage and exact item IDs;
    an absent line means zero only after that complete read. This helper cannot
    authenticate the capture, establish DOM coverage or prove a successful click.
    Caller loads current owned state and submits the result through normal runtime CAS.
    """
    try:
        run = RunState.model_validate(run.model_dump(warnings=False))
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_READ_CHECKPOINT") from None
    if run.principal != principal:
        raise PageEvidenceError("READ_ACCESS_DENIED")
    if run.version != expected_run_version:
        raise PageEvidenceError("STALE_READ_VERSION")
    action = run.pending
    if action is None or action.tool.name != "read_cart":
        raise PageEvidenceError("PENDING_CART_READ_REQUIRED")
    try:
        cart = CartSnapshot.model_validate(cart.model_dump())
        target = BrowserTarget.model_validate(target.model_dump())
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_CART_CAPTURE") from None
    if not target.visible or (target.tab_id, target.document_id, cart.document_id) != (
        action.target.tab_id,
        action.target.document_id,
        action.target.document_id,
    ):
        raise PageEvidenceError("READ_TARGET_MISMATCH")
    if coverage != "complete":
        raise PageEvidenceError("CART_COVERAGE_INCOMPLETE")
    config = config or AIConfig()
    if (
        now.tzinfo is None
        or not action.issued_at <= cart.observed_at <= now < action.expires_at
        or (now - cart.observed_at).total_seconds() > config.max_snapshot_age_seconds
    ):
        raise PageEvidenceError("READ_CAPTURE_OUTSIDE_ACTION_WINDOW")
    return BrowserResult(
        action_id=action.action_id,
        run_id=run.run_id,
        session_id=principal.session_id,
        run_version=action.run_version,
        source_document_id=action.target.document_id,
        target=target,
        observed_at=cart.observed_at,
        outcome="observed",
        cart=cart,
    )


def _normal(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else None


def _name(value):
    return value.get("name") if isinstance(value, dict) else value


def _verify_product_identity(product, offer, identity: ProductIdentity):
    if (_normal(_name(product.get("brand"))), _normal(_name(product.get("model")))) != (
        _normal(identity.brand),
        _normal(identity.model),
    ):
        raise PageEvidenceError("OBSERVED_BRAND_MODEL_UNVERIFIED")
    condition = {
        "new": "NewCondition",
        "used": "UsedCondition",
        "refurbished": "RefurbishedCondition",
    }[identity.condition]
    item_condition = offer.get("itemCondition", product.get("itemCondition"))
    if (
        "itemCondition" in offer
        and "itemCondition" in product
        and offer["itemCondition"] != product["itemCondition"]
    ):
        raise PageEvidenceError("CONFLICTING_ITEM_CONDITION")
    if item_condition not in (f"https://schema.org/{condition}", f"http://schema.org/{condition}"):
        raise PageEvidenceError("OBSERVED_CONDITION_UNVERIFIED")
    attributes = {}
    for key in ("color", "size"):
        if key in product:
            attributes[key] = _normal(product[key])
    properties = product.get("additionalProperty", [])
    if isinstance(properties, dict):
        properties = [properties]
    if not isinstance(properties, list):
        raise PageEvidenceError("AMBIGUOUS_IDENTITY_PROPERTIES")
    for entry in properties:
        if not isinstance(entry, dict) or not _has_type(entry, "PropertyValue"):
            raise PageEvidenceError("AMBIGUOUS_IDENTITY_PROPERTIES")
        key, value = _normal(entry.get("name")), _normal(entry.get("value"))
        if key == "colour":
            key = "color"
        if not key or value is None or key in attributes:
            raise PageEvidenceError("AMBIGUOUS_IDENTITY_PROPERTIES")
        attributes[key] = value
    for label, value in identity.attributes.items():
        key = "color" if _normal(label) == "colour" else _normal(label)
        if attributes.get(key) != _normal(value):
            raise PageEvidenceError("OBSERVED_VARIANT_UNVERIFIED")


def build_market_offer(
    search: MarketSearch,
    principal: Principal,
    task_id: str,
    page: CapturedPage,
    details: ObservedOfferDetails,
    *,
    expected_search_version: int,
    offer_id: str,
    snapshot_id: str,
    now: datetime,
    config: AIConfig | None = None,
) -> Offer:
    """Normalize one observation, not task completion or a verified cheapest claim.

    Delivery-scoped fees must be supplied by the trusted visible executor. Missing
    fees remain unknown. Observed identity must be independently corroborated by
    explicit JSON-LD brand/model/condition/variant fields; never copied from search.
    """
    try:
        search = MarketSearch.model_validate(search.model_dump())
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_MARKET_CHECKPOINT") from None
    if (search.owner_id, search.session_id) != (principal.owner_id, principal.session_id):
        raise PageEvidenceError("MARKET_CAPTURE_ACCESS_DENIED")
    if search.version != expected_search_version:
        raise PageEvidenceError("STALE_MARKET_CAPTURE_VERSION")
    task = next((item for item in search.tasks if item.task_id == task_id), None)
    if task is None or any(receipt.task_id == task_id for receipt in search.receipts):
        raise PageEvidenceError("PENDING_SEARCH_TASK_REQUIRED")
    try:
        page = CapturedPage.model_validate(page.model_dump() | {"html": page.html})
        details = ObservedOfferDetails.model_validate(details.model_dump())
        if (
            now.tzinfo is None
            or not search.created_at <= page.observed_at <= now < task.deadline_at
        ):
            raise PageEvidenceError("MARKET_CAPTURE_OUTSIDE_TASK_WINDOW")
        if (details.document_id, details.observed_at, details.delivery_context) != (
            page.target.document_id,
            page.observed_at,
            search.delivery_context,
        ):
            raise PageEvidenceError("OFFER_DETAILS_SCOPE_MISMATCH")
        origin, url = urlsplit(task.store.origin), urlsplit(page.url)
        if (origin.scheme, origin.netloc) != (url.scheme, url.netloc):
            raise PageEvidenceError("MARKET_CAPTURE_ORIGIN_MISMATCH")
        snapshot = extract_product_snapshot(
            page, snapshot_id=snapshot_id, product_id=offer_id, now=now, config=config
        )
        if snapshot.price is None or snapshot.currency != details.currency:
            raise PageEvidenceError("OFFER_PRICE_CURRENCY_UNVERIFIED")
        product, offer = _product_offer(page)
        _verify_product_identity(product, offer, details.identity)
        if _normal(_name(offer.get("seller"))) != _normal(details.seller):
            raise PageEvidenceError("OBSERVED_SELLER_UNVERIFIED")
        return Offer(
            offer_id=offer_id,
            snapshot_id=snapshot_id,
            identity=details.identity,
            url=urldefrag(page.url)[0],
            seller=details.seller,
            observed_at=page.observed_at,
            in_stock=snapshot.in_stock,
            price=snapshot.price,
            currency=snapshot.currency,
            shipping=details.shipping,
            extra_tax_and_fees=details.extra_tax_and_fees,
            conditional_discount_note=details.conditional_discount_note,
        )
    except PageEvidenceError:
        raise
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        raise PageEvidenceError("INVALID_MARKET_PAGE_EVIDENCE") from None


def build_navigation_result(
    proposal: OfferNavigation,
    principal: Principal,
    page: CapturedPage,
    observed_identity: ProductIdentity,
    *,
    snapshot_id: str,
    product_id: str,
    now: datetime,
    config: AIConfig | None = None,
) -> NavigationReceipt:
    """Normalize actual opened-page evidence; never navigate or reuse old prices.

    Load the current pending proposal from trusted state. Submitting through the
    runtime with its current conversation version remains mandatory.
    """
    try:
        proposal = OfferNavigation.model_validate(proposal.model_dump())
    except (ValueError, TypeError):
        raise PageEvidenceError("INVALID_NAVIGATION_PROPOSAL") from None
    if (proposal.owner_id, proposal.session_id) != (principal.owner_id, principal.session_id):
        raise PageEvidenceError("NAVIGATION_CAPTURE_ACCESS_DENIED")
    try:
        page = CapturedPage.model_validate(page.model_dump() | {"html": page.html})
        observed_identity = ProductIdentity.model_validate(observed_identity.model_dump())
        if page.url != proposal.offer.url or page.target.tab_id != proposal.source.tab_id:
            raise PageEvidenceError("NAVIGATION_CAPTURE_TARGET_MISMATCH")
        if (
            now.tzinfo is None
            or not proposal.created_at <= page.observed_at <= now < proposal.expires_at
        ):
            raise PageEvidenceError("NAVIGATION_CAPTURE_OUTSIDE_WINDOW")
        if not proposal.offer.identity.matches(observed_identity):
            raise PageEvidenceError("NAVIGATION_OBSERVED_IDENTITY_MISMATCH")
        snapshot = extract_product_snapshot(
            page, snapshot_id=snapshot_id, product_id=product_id, now=now, config=config
        )
        product, offer = _product_offer(page)
        _verify_product_identity(product, offer, observed_identity)
        if _normal(_name(offer.get("seller"))) != _normal(proposal.offer.seller):
            raise PageEvidenceError("NAVIGATION_SELLER_UNVERIFIED")
        return NavigationReceipt(
            action_id=proposal.action_id,
            search_id=proposal.search_id,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            status="opened",
            observed_at=page.observed_at,
            url=page.url,
            target=page.target,
            identity=observed_identity,
            product=snapshot,
        )
    except PageEvidenceError:
        raise
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        raise PageEvidenceError("INVALID_NAVIGATION_PAGE_EVIDENCE") from None
