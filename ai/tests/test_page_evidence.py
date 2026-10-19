import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from shopping_ai import AIRequest, ShoppingAI
from shopping_ai.browser import BrowserTarget
from shopping_ai.page_evidence import (
    CapturedPage,
    ObservedOfferDetails,
    PageEvidenceError,
    build_market_offer,
    build_product_read_result,
    decode_captured_page,
    extract_product_snapshot,
)
from shopping_ai.providers import MockProvider

URL = "https://store.example/product/one"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"html":"SECRET","html":"duplicate"}',
        b'{"price":NaN}',
        b"\xff",
        b"[" * 1500 + b"]" * 1500,
    ],
)
def test_shared_capture_decoder_rejects_bad_records_without_echoing_content(payload):
    with pytest.raises(PageEvidenceError) as error:
        decode_captured_page(payload)
    assert str(error.value) == "INVALID_CAPTURE_PAYLOAD"


def product(**changes):
    return {
        "@type": "Product",
        "@id": URL + "#product",
        "name": "Demo phone",
        "url": URL,
        "offers": {
            "@type": "Offer",
            "url": URL,
            "price": "100.10",
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock",
        },
    } | changes


def capture(now, node=None, **changes):
    return CapturedPage(
        **(
            {
                "target": BrowserTarget(
                    tab_id=1, document_id="doc", active=True, window_focused=True
                ),
                "url": URL,
                "observed_at": now,
                "visible_title": "Demo phone",
                "visible_price": "100.10",
                "visible_currency": "INR",
                "visible_stock": True,
                "html": '<script type="application/ld+json">'
                + json.dumps(node or product())
                + "</script>",
            }
            | changes
        )
    )


def extract(page, now):
    return extract_product_snapshot(page, snapshot_id="captured", product_id="product", now=now)


@pytest.fixture
def read_run(now):
    from shopping_ai import Orchestrator, Principal

    orchestrator = Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now))
    principal = Principal(owner_id="owner", session_id="session")
    run = orchestrator.start(
        principal,
        AIRequest(request_id="read", message="price", available_tools=("read_product",)),
        capture(now).target,
    )
    run = asyncio.run(orchestrator.advance(run, principal, expected_version=run.version))
    return orchestrator, principal, run


@pytest.mark.parametrize(
    "change,code",
    [
        ("owner", "READ_ACCESS_DENIED"),
        ("session", "READ_ACCESS_DENIED"),
        ("version", "STALE_READ_VERSION"),
        ("tab", "READ_TARGET_MISMATCH"),
        ("document", "READ_TARGET_MISMATCH"),
        ("before_issue", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("future", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("expired", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
    ],
)
def test_read_builder_rejects_foreign_obsolete_or_mistargeted_captures(now, read_run, change, code):
    _, principal, run = read_run
    page = capture(now)
    version = run.version
    observed_now = now
    if change in {"owner", "session"}:
        principal = principal.model_copy(
            update={"owner_id" if change == "owner" else "session_id": "other"}
        )
    elif change == "version":
        version += 1
    elif change in {"tab", "document"}:
        target = page.target.model_copy(
            update={"tab_id": 2} if change == "tab" else {"document_id": "other"}
        )
        page = capture(now, target=target)
    elif change in {"before_issue", "future"}:
        page = capture(
            now, observed_at=now + timedelta(seconds=-1 if change == "before_issue" else 1)
        )
    else:
        observed_now = run.pending.expires_at
    with pytest.raises(PageEvidenceError, match=code):
        build_product_read_result(
            run,
            principal,
            page,
            expected_run_version=version,
            snapshot_id="snapshot",
            product_id="product",
            now=observed_now,
        )
    assert run.status == "awaiting_browser" and run.pending is not None


def test_read_builder_never_converts_product_html_into_cart_or_variant_receipts(now, read_run):
    from shopping_ai.models import ToolProposal

    _, principal, run = read_run
    for tool in (
        ToolProposal(name="read_cart"),
        ToolProposal(name="select_variant", product_id="product", variant_id="black"),
    ):
        altered = run.model_copy(update={"pending": run.pending.model_copy(update={"tool": tool})})
        with pytest.raises(PageEvidenceError, match="PENDING_PRODUCT_READ_REQUIRED"):
            build_product_read_result(
                altered,
                principal,
                capture(now),
                expected_run_version=altered.version,
                snapshot_id="snapshot",
                product_id="product",
                now=now,
            )


def test_cancelled_read_can_record_bound_evidence_without_reviving_run(now, read_run):
    orchestrator, principal, run = read_run
    cancelled = orchestrator.cancel(run, principal, expected_version=run.version)
    result = build_product_read_result(
        cancelled,
        principal,
        capture(now),
        expected_run_version=cancelled.version,
        snapshot_id="snapshot",
        product_id="product",
        now=now,
    )
    assert result.run_version == run.pending.run_version < cancelled.version
    recorded = orchestrator.accept_result(
        cancelled, principal, result, expected_version=cancelled.version
    )
    assert recorded.status == "cancelled" and recorded.pending is None


def test_captured_evidence_reaches_existing_grounded_ai_without_variant_inference(now):
    page = capture(now, {"@context": "https://schema.org", "@graph": [product()]})
    snapshot = extract(page, now)
    assert snapshot.price == Decimal("100.10") and snapshot.in_stock is True
    assert snapshot.document_id == page.target.document_id and snapshot.observed_at == now
    assert snapshot.selected_variant_id is None and not snapshot.variants
    response = asyncio.run(
        ShoppingAI(MockProvider(), clock=lambda: now).decide(
            AIRequest(request_id="question", message="price and stock", product=snapshot)
        )
    )
    assert response.status == "answered" and "INR 100.10" in response.message
    assert response.tool is None
    assert "html" not in page.model_dump() and "<script" not in repr(page)


def test_normalized_page_can_complete_runtime_read_product_receipt_flow(now):
    from shopping_ai import Conversation, Orchestrator, Principal
    from shopping_ai.runtime import AssistantRuntime

    async def scenario():
        runtime = AssistantRuntime(
            Conversation(Orchestrator(ShoppingAI(MockProvider(), clock=lambda: now)))
        )
        principal = Principal(owner_id="owner", session_id="session")
        page = capture(now)
        state = (await runtime.start(principal, language="en")).state
        pending = await runtime.handle(
            state,
            principal,
            {
                "kind": "text",
                "turn": {
                    "request_id": "read",
                    "message": "price and stock",
                    "target": page.target,
                    "available_tools": ("read_product",),
                },
            },
            expected_version=state.version,
        )
        action = pending.conversation_reply.action
        assert action.tool.name == "read_product"
        run = pending.state.active_run
        result = build_product_read_result(
            run,
            principal,
            page,
            expected_run_version=run.version,
            snapshot_id="captured",
            product_id="product",
            now=now,
        )
        observed = await runtime.handle(
            pending.state,
            principal,
            {
                "kind": "browser_result",
                "result": result,
            },
            expected_version=pending.state.version,
        )
        final = await runtime.handle(
            observed.state, principal, {"kind": "advance"}, expected_version=observed.state.version
        )
        assert final.conversation_reply.status == "complete"
        assert "INR 100.10" in final.conversation_reply.message
        assert final.conversation_reply.action is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change,code",
    [
        ({"visible_title": "Other phone"}, "VISIBLE_TITLE_MISMATCH"),
        ({"visible_price": "100.11"}, "VISIBLE_PRICE_MISMATCH"),
        ({"visible_currency": "USD"}, "VISIBLE_PRICE_MISMATCH"),
        ({"visible_stock": False}, "VISIBLE_STOCK_MISMATCH"),
    ],
)
def test_contradictory_visible_evidence_is_rejected(now, change, code):
    with pytest.raises(PageEvidenceError, match=code):
        extract(capture(now, **change), now)


def test_metadata_alone_never_establishes_price_or_stock(now):
    snapshot = extract(
        capture(now, visible_price=None, visible_currency=None, visible_stock=None), now
    )
    assert snapshot.price is snapshot.currency is snapshot.in_stock is None


@pytest.mark.parametrize("availability", [None, "https://schema.org/PreOrder", "InStock"])
def test_unknown_availability_is_not_assumed_available(now, availability):
    node = product()
    node["offers"]["availability"] = availability
    assert extract(capture(now, node), now).in_stock is None


@pytest.mark.parametrize(
    "node,code",
    [
        ([product(), product(name="Other phone")], "SINGLE_PRODUCT_REQUIRED"),
        (
            product(offers={"@type": "AggregateOffer", "lowPrice": "1", "priceCurrency": "INR"}),
            "SINGLE_OFFER_REQUIRED",
        ),
        (product(offers=[product()["offers"], product()["offers"]]), "SINGLE_OFFER_REQUIRED"),
        (product(url="https://other.example/one"), "PRODUCT_URL_MISMATCH"),
    ],
)
def test_ambiguous_products_price_ranges_and_foreign_urls_do_not_become_snapshots(now, node, code):
    with pytest.raises(PageEvidenceError, match=code):
        extract(capture(now, node), now)


@pytest.mark.parametrize(
    "raw,code",
    [
        ('{"@type":"Product","name":"SECRET","name":"Other"}', "DUPLICATE_JSONLD_KEY"),
        ('{"price":NaN}', "NONFINITE_JSONLD_NUMBER"),
        ('{"broken":"SECRET"', "INVALID_PAGE_EVIDENCE"),
    ],
)
def test_malformed_metadata_errors_never_echo_page_content(now, raw, code):
    page = capture(now, html='<script type="application/ld+json">' + raw + "</script>")
    with pytest.raises(PageEvidenceError) as error:
        extract(page, now)
    assert str(error.value) == code and "SECRET" not in str(error.value)


def test_stale_future_or_hidden_capture_cannot_supply_fresh_evidence(now):
    for observed in (now - timedelta(days=1), now + timedelta(seconds=1)):
        with pytest.raises(PageEvidenceError, match="STALE_PAGE_CAPTURE"):
            extract(capture(now, observed_at=observed), now)
    with pytest.raises(ValueError, match="visible target"):
        capture(
            now,
            target=BrowserTarget(tab_id=1, document_id="doc", active=False, window_focused=True),
        )


def test_script_limits_external_scripts_and_truncated_capture_fail_closed(now):
    for html, code in (
        ('<script type="application/ld+json" src=""></script>', "EXTERNAL_JSONLD_UNSUPPORTED"),
        ('<script type="application/ld+json">{', "INCOMPLETE_JSONLD_SCRIPT"),
        (
            '<script type="text/plain" type="application/ld+json"></script>',
            "AMBIGUOUS_SCRIPT_ATTRIBUTES",
        ),
        ('<script type="application/ld+json">{}</script>' * 17, "TOO_MANY_JSONLD_BLOCKS"),
    ):
        with pytest.raises(PageEvidenceError, match=code):
            extract(capture(now, html=html), now)


@pytest.fixture
def market_page(now):
    from shopping_ai import Principal
    from shopping_ai.market import ProductIdentity, Store, plan_search

    principal = Principal(owner_id="owner", session_id="session")
    identity = ProductIdentity(
        brand="Demo", model="Phone", attributes={"colour": "black", "storage": "128GB"}
    )
    search = plan_search(
        search_id="search",
        owner_id=principal.owner_id,
        session_id=principal.session_id,
        identity=identity,
        currency="INR",
        delivery_context="201001",
        now=now,
        stores=tuple(
            Store(store_id=name, origin=origin, search_path="/search", query_parameter="q")
            for name, origin in (
                ("first", "https://store.example"),
                ("second", "https://other.example"),
            )
        ),
    )
    node = product(
        brand={"@type": "Brand", "name": "Demo"},
        model="Phone",
        color="black",
        additionalProperty=[{"@type": "PropertyValue", "name": "storage", "value": "128GB"}],
    )
    node["offers"].update(
        seller={"@type": "Organization", "name": "Shop"},
        itemCondition="https://schema.org/NewCondition",
    )
    details = ObservedOfferDetails(
        document_id="doc",
        observed_at=now,
        identity=identity,
        seller="Shop",
        delivery_context="201001",
        currency="INR",
        shipping="10",
        extra_tax_and_fees="0",
    )
    return search, principal, node, details


def make_offer(now, search, principal, node, details, index=0):
    url = search.tasks[index].store.origin + "/product/one"
    node = json.loads(json.dumps(node))
    node["url"] = url
    node["offers"]["url"] = url
    page = capture(now, node, url=url, visible_price=node["offers"]["price"])
    return build_market_offer(
        search,
        principal,
        search.tasks[index].task_id,
        page,
        details,
        expected_search_version=search.version,
        offer_id=f"offer{index}",
        snapshot_id=f"snap{index}",
        now=now,
    )


def test_captured_market_offers_feed_delivered_cost_ranking_without_coupon_deduction(
    now, market_page
):
    from shopping_ai.config import AIConfig
    from shopping_ai.market import SearchReceipt, accept_search_receipt, rank_offers

    search, principal, node, details = market_page
    for index in range(2):
        observed = details.model_copy(update={"conditional_discount_note": "Bank card 20 off"})
        if index:
            node["offers"]["price"] = "105.10"
            observed = observed.model_copy(update={"shipping": Decimal("0")})
        offer = make_offer(now, search, principal, node, observed, index)
        receipt = SearchReceipt(
            search_id=search.search_id,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            task_id=search.tasks[index].task_id,
            delivery_context=search.delivery_context,
            status="complete",
            offers=(offer,),
        )
        search = accept_search_receipt(
            search,
            receipt,
            owner_id=principal.owner_id,
            session_id=principal.session_id,
            expected_version=search.version,
            now=now,
        )
    result = rank_offers(search, now=now, config=AIConfig())
    assert result.cheapest_offer_ids == ("offer1",)
    assert [item.total for item in result.ranked] == [Decimal("105.10"), Decimal("110.10")]


def test_market_builder_preserves_unknown_fees_and_observed_mismatched_variant(now, market_page):
    search, principal, node, details = market_page
    node["color"] = "blue"
    identity = details.identity.model_copy(
        update={"attributes": {"colour": "blue", "storage": "128GB"}}
    )
    observed = details.model_copy(
        update={"identity": identity, "shipping": None, "extra_tax_and_fees": None}
    )
    offer = make_offer(now, search, principal, node, observed)
    assert offer.identity == identity and not search.identity.matches(offer.identity)
    assert offer.total is None and offer.shipping is None
    with pytest.raises(PageEvidenceError, match="OBSERVED_VARIANT_UNVERIFIED"):
        make_offer(
            now, search, principal, node, details
        )  # Requested black cannot replace observed blue.


@pytest.mark.parametrize(
    "field,code",
    [
        ("brand", "OBSERVED_BRAND_MODEL_UNVERIFIED"),
        ("model", "OBSERVED_BRAND_MODEL_UNVERIFIED"),
        ("color", "OBSERVED_VARIANT_UNVERIFIED"),
        ("itemCondition", "OBSERVED_CONDITION_UNVERIFIED"),
        ("seller", "OBSERVED_SELLER_UNVERIFIED"),
    ],
)
def test_missing_explicit_market_identity_is_not_guessed_from_title(now, market_page, field, code):
    search, principal, node, details = market_page
    (node["offers"] if field in {"itemCondition", "seller"} else node).pop(field)
    with pytest.raises(PageEvidenceError, match=code):
        make_offer(now, search, principal, node, details)


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "different"),
        ("delivery_context", "110001"),
        ("observed_at", "older"),
    ],
)
def test_offer_costs_are_bound_to_same_capture_and_delivery_context(now, market_page, field, value):
    search, principal, node, details = market_page
    if value == "older":
        value = now - timedelta(seconds=1)
    with pytest.raises(PageEvidenceError, match="OFFER_DETAILS_SCOPE_MISMATCH"):
        make_offer(now, search, principal, node, details.model_copy(update={field: value}))


def test_market_capture_rejects_foreign_owner_origin_and_expired_task(now, market_page):
    search, principal, node, details = market_page
    page = capture(now, node)
    kwargs = dict(
        expected_search_version=search.version, offer_id="offer", snapshot_id="snap", now=now
    )
    for who, altered, changes, code in (
        (
            principal.model_copy(update={"owner_id": "other"}),
            page,
            {},
            "MARKET_CAPTURE_ACCESS_DENIED",
        ),
        (
            principal,
            page,
            {"expected_search_version": search.version + 1},
            "STALE_MARKET_CAPTURE_VERSION",
        ),
        (
            principal,
            capture(now, node, url="https://foreign.example/product"),
            {},
            "MARKET_CAPTURE_ORIGIN_MISMATCH",
        ),
        (principal, page, {"now": search.deadline_at}, "MARKET_CAPTURE_OUTSIDE_TASK_WINDOW"),
    ):
        with pytest.raises(PageEvidenceError, match=code):
            build_market_offer(
                search, who, search.tasks[0].task_id, altered, details, **(kwargs | changes)
            )


def variant_capture(now, *, markup=None, **control_changes):
    from shopping_ai.page_evidence import ObservedVariantControl

    page = capture(now)
    control = ObservedVariantControl.model_validate(
        {
            "control_id": "variants",
            "document_id": "doc",
            "observed_at": now,
            "variants": [
                {"variant_id": "black", "label": "Black"},
                {"variant_id": "blue", "label": "Blue"},
            ],
            "selected_variant_id": "blue",
            "price_variant_id": "blue",
        }
        | control_changes
    )
    html = (
        markup
        if markup is not None
        else (
            '<select id="variants" name="id">'
            '<option value="black" selected>Black</option>'
            '<option value="blue">Blue</option></select>'
        )
    )
    return page.model_copy(update={"html": page.html + html, "variant_control": control})


def test_live_variant_selection_overrides_stale_html_default_and_reaches_ai(now):
    snapshot = extract(variant_capture(now), now)
    assert snapshot.selected_variant_id == "blue"
    assert [v.variant_id for v in snapshot.variants] == ["black", "blue"]
    reply = asyncio.run(
        ShoppingAI(MockProvider(), clock=lambda: now).decide(
            AIRequest(request_id="variant-facts", message="price", product=snapshot)
        )
    )
    assert reply.status == "answered" and "INR 100.10" in reply.message
    assert reply.tool is None


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"document_id": "other"}, "VARIANT_CAPTURE_MISMATCH"),
        ({"observed_at": None}, "VARIANT_CAPTURE_MISMATCH"),
        ({"price_variant_id": None}, "VARIANT_PRICE_BINDING_REQUIRED"),
        ({"selected_variant_id": None, "price_variant_id": None}, "VARIANT_PRICE_BINDING_REQUIRED"),
    ],
)
def test_variant_capture_scope_and_price_binding(now, changes, code):
    if changes.get("observed_at", "missing") is None:
        changes = {"observed_at": now - timedelta(seconds=1)}
    with pytest.raises(PageEvidenceError, match=code):
        extract(variant_capture(now, **changes), now)


@pytest.mark.parametrize(
    "markup",
    [
        "",
        '<select id="variants" name="id"></select>' * 2,
        '<select id="variants" name="color"></select>',
        '<select id="variants" name="id" disabled></select>',
        '<select id="variants" name="id" multiple></select>',
        '<select id="variants" name="id"><option value="black">Black</option></select>',
        (
            '<select id="variants" name="id"><option value="black">Black</option>'
            '<option value="black">Blue</option></select>'
        ),
        (
            '<select id="variants" name="id"><option value="black" disabled>Black</option>'
            '<option value="blue">Blue</option></select>'
        ),
        (
            '<select id="variants" name="id"><optgroup disabled>'
            '<option value="black">Black</option></optgroup></select>'
        ),
        '<select id="variants" name="id"><option value="black">Black',
    ],
)
def test_variant_control_rejects_unsupported_or_disagreeing_html(now, markup):
    with pytest.raises(PageEvidenceError):
        extract(variant_capture(now, markup=markup), now)


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_variant_id": "missing"},
        {"price_variant_id": "black"},
        {"variants": [{"variant_id": "black", "label": "Black"}] * 2},
    ],
)
def test_variant_record_rejects_unknown_selection_and_duplicate_ids(now, changes):
    with pytest.raises(ValueError):
        variant_capture(now, **changes)


def test_captured_variant_observation_obeys_existing_cart_authorization(now, grant):
    from shopping_ai.context import BuiltContext
    from shopping_ai.models import ToolProposal
    from shopping_ai.policy import PolicyViolation, validate_tool

    snapshot = extract(variant_capture(now), now)
    grant = grant.model_copy(
        update={
            "product_id": snapshot.product_id,
            "document_id": snapshot.document_id,
            "variant_id": "blue",
        }
    )
    request = AIRequest(
        request_id=grant.request_id,
        message="Add blue to cart",
        product=snapshot,
        available_tools=("add_to_cart",),
        authorization=grant,
    )
    proposal = ToolProposal(
        name="add_to_cart",
        product_id=snapshot.product_id,
        variant_id="blue",
        quantity=1,
        mode="ensure_quantity",
    )
    context = BuiltContext(payload="{}", facts=(), fresh=True)
    validate_tool(proposal, request, context, now)
    with pytest.raises(PolicyViolation) as error:
        validate_tool(proposal, request.model_copy(update={"authorization": None}), context, now)
    assert error.value.code == "AUTHORIZATION_REQUIRED"
    with pytest.raises(PolicyViolation) as error:
        validate_tool(proposal.model_copy(update={"variant_id": "black"}), request, context, now)
    assert error.value.code == "TARGET_MISMATCH"


@pytest.fixture
def cart_read_run(read_run):
    from shopping_ai.models import ToolProposal

    orchestrator, principal, run = read_run
    run = run.model_copy(
        update={"pending": run.pending.model_copy(update={"tool": ToolProposal(name="read_cart")})}
    )
    return orchestrator, principal, run


@pytest.mark.parametrize(
    "change,code",
    [
        ("owner", "READ_ACCESS_DENIED"),
        ("session", "READ_ACCESS_DENIED"),
        ("version", "STALE_READ_VERSION"),
        ("tab", "READ_TARGET_MISMATCH"),
        ("document", "READ_TARGET_MISMATCH"),
        ("cart_document", "READ_TARGET_MISMATCH"),
        ("hidden", "READ_TARGET_MISMATCH"),
        ("before_issue", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("future", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("expired", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("naive", "READ_CAPTURE_OUTSIDE_ACTION_WINDOW"),
        ("wrong_tool", "PENDING_CART_READ_REQUIRED"),
    ],
)
def test_cart_read_builder_rejects_unbound_observations(now, cart_read_run, change, code):
    from shopping_ai.browser import CartSnapshot
    from shopping_ai.models import ToolProposal
    from shopping_ai.page_evidence import build_cart_read_result

    _, principal, run = cart_read_run
    target = run.pending.target
    cart = CartSnapshot(observed_at=now, document_id=target.document_id)
    version = run.version
    clock = now
    if change in {"owner", "session"}:
        key = "owner_id" if change == "owner" else "session_id"
        principal = principal.model_copy(update={key: "foreign"})
    elif change == "version":
        version += 1
    elif change == "tab":
        target = target.model_copy(update={"tab_id": target.tab_id + 1})
    elif change == "document":
        target = target.model_copy(update={"document_id": "foreign"})
    elif change == "cart_document":
        cart = cart.model_copy(update={"document_id": "foreign"})
    elif change == "hidden":
        target = target.model_copy(update={"active": False})
    elif change in {"before_issue", "future"}:
        cart = cart.model_copy(
            update={"observed_at": now + timedelta(seconds=1 if change == "future" else -1)}
        )
    elif change == "expired":
        clock = run.pending.expires_at
    elif change == "naive":
        clock = now.replace(tzinfo=None)
    else:
        run = run.model_copy(
            update={
                "pending": run.pending.model_copy(
                    update={"tool": ToolProposal(name="read_product")}
                )
            }
        )
    with pytest.raises(PageEvidenceError, match=code):
        build_cart_read_result(
            run,
            principal,
            cart,
            target,
            coverage="complete",
            expected_run_version=version,
            now=clock,
        )


def test_cancelled_cart_read_preserves_original_action_binding(now, cart_read_run):
    from shopping_ai.browser import CartSnapshot
    from shopping_ai.page_evidence import build_cart_read_result

    orchestrator, principal, run = cart_read_run
    cancelled = orchestrator.cancel(run, principal, expected_version=run.version)
    receipt = build_cart_read_result(
        cancelled,
        principal,
        CartSnapshot(observed_at=now, document_id=run.pending.target.document_id),
        run.pending.target,
        coverage="complete",
        expected_run_version=cancelled.version,
        now=now,
    )
    assert receipt.run_version == run.pending.run_version < cancelled.version
    recorded = orchestrator.accept_result(
        cancelled, principal, receipt, expected_version=cancelled.version
    )
    assert recorded.status == "cancelled" and recorded.pending is None
    assert receipt.cart.lines == () and receipt.product is None


def test_cart_read_builder_cannot_synthesize_mutation_receipt(now, cart_read_run):
    from shopping_ai.browser import CartSnapshot
    from shopping_ai.models import ToolProposal
    from shopping_ai.page_evidence import build_cart_read_result

    _, principal, run = cart_read_run
    run = run.model_copy(
        update={
            "pending": run.pending.model_copy(
                update={
                    "tool": ToolProposal(
                        name="add_to_cart",
                        product_id="p42",
                        variant_id="black",
                        quantity=1,
                        mode="ensure_quantity",
                    )
                }
            )
        }
    )
    with pytest.raises(PageEvidenceError, match="PENDING_CART_READ_REQUIRED"):
        build_cart_read_result(
            run,
            principal,
            CartSnapshot(observed_at=now, document_id=run.target.document_id),
            run.target,
            coverage="complete",
            expected_run_version=run.version,
            now=now,
        )


@pytest.mark.parametrize("coverage", ["partial", "unknown", None, True, "COMPLETE"])
@pytest.mark.parametrize("quantity", [0, 1])
def test_cart_builder_never_uses_incomplete_cart_as_quantity_evidence(
    now, cart_read_run, coverage, quantity
):
    from shopping_ai.browser import CartLine, CartSnapshot
    from shopping_ai.page_evidence import build_cart_read_result

    _, principal, run = cart_read_run
    cart = CartSnapshot(
        observed_at=now,
        document_id=run.target.document_id,
        lines=()
        if quantity == 0
        else (CartLine(product_id="p42", variant_id="black", quantity=1),),
    )
    with pytest.raises(PageEvidenceError, match="CART_COVERAGE_INCOMPLETE"):
        build_cart_read_result(
            run,
            principal,
            cart,
            run.target,
            coverage=coverage,
            expected_run_version=run.version,
            now=now,
        )
    assert run.pending is not None and run.status == "awaiting_browser"


def test_cart_capture_decoder_roundtrip_preserves_unknown_coverage(now):
    from shopping_ai.browser import CartSnapshot
    from shopping_ai.page_evidence import CapturedCart, decode_captured_cart

    observed = CapturedCart(
        target=capture(now).target,
        cart=CartSnapshot(observed_at=now, document_id="doc"),
        coverage="unknown",
    )
    decoded = decode_captured_cart(observed.model_dump_json().encode())
    assert decoded == observed and decoded.coverage == "unknown"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"coverage":"complete","coverage":"unknown","private":"SECRET"}',
        b'{"coverage":NaN}',
        b"\xff",
        b"{}",
        b"x" * 262145,
        b"[" * 1500 + b"]" * 1500,
    ],
)
def test_cart_capture_decoder_rejects_bad_payload_without_private_content(payload):
    from shopping_ai.page_evidence import decode_captured_cart

    with pytest.raises(PageEvidenceError) as error:
        decode_captured_cart(payload)
    assert str(error.value) == "INVALID_CART_CAPTURE_PAYLOAD"


@pytest.mark.parametrize("change", ["document", "duplicate_lines", "missing_coverage", "quantity"])
def test_cart_capture_decoder_validates_scope_line_identity_and_coverage(now, change):
    from shopping_ai.browser import CartSnapshot
    from shopping_ai.page_evidence import CapturedCart, decode_captured_cart

    record = CapturedCart(
        target=capture(now).target,
        cart=CartSnapshot(observed_at=now, document_id="doc"),
        coverage="complete",
    ).model_dump(mode="json")
    line = {"product_id": "p42", "variant_id": "black", "quantity": 1}
    if change == "document":
        record["cart"]["document_id"] = "foreign"
    elif change == "duplicate_lines":
        record["cart"]["lines"] = [line, line]
    elif change == "missing_coverage":
        del record["coverage"]
    else:
        record["cart"]["lines"] = [line | {"quantity": "1"}]
    with pytest.raises(PageEvidenceError, match="INVALID_CART_CAPTURE_PAYLOAD"):
        decode_captured_cart(json.dumps(record).encode())
