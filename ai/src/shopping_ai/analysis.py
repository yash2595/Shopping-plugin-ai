"""Local, extractive shopping help. Observations are data, never model authority."""

import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, field_validator, model_validator

from shopping_ai.models import AIResponse, Contract, Identifier, Money, ProductSnapshot, ShortText


class ReviewObservation(Contract):
    review_id: Identifier
    rating: Annotated[int, Field(strict=True, ge=1, le=5)]
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    verified_purchase_observed: StrictBool | None = None


class AnalysisProduct(Contract):
    product: ProductSnapshot
    source_url: Annotated[str, Field(max_length=2000)]
    reviews: tuple[ReviewObservation, ...] = Field(default=(), max_length=30)

    @field_validator("source_url")
    @classmethod
    def inert_https_source(cls, value):
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("A captured HTTPS product source is required")
        return value

    @model_validator(mode="after")
    def distinct_reviews(self):
        if len({r.review_id for r in self.reviews}) != len(self.reviews):
            raise ValueError("Duplicate review identifiers")
        if sum(len(r.text) for r in self.reviews) > 15000:
            raise ValueError("Product review evidence exceeds the request budget")
        return self


class AnalysisRequest(Contract):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    products: tuple[AnalysisProduct, ...] = Field(default=(), max_length=4)
    budget: Money | None = None
    selected_product_id: Identifier | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"
    required_specs: dict[Annotated[str, Field(min_length=1, max_length=80)], ShortText] = Field(
        default_factory=dict, max_length=8
    )
    language: Literal["en", "hi-Latn"] = "hi-Latn"

    @model_validator(mode="after")
    def bounded_evidence(self):
        if not self.question.strip():
            raise ValueError("A shopping question is required")
        ids = [p.product.product_id for p in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("Product references must be distinct")
        if self.selected_product_id is not None and self.selected_product_id not in ids:
            raise ValueError("Selected product must belong to the supplied captures")
        if sum(len(r.text) for p in self.products for r in p.reviews) > 60000:
            raise ValueError("Review evidence exceeds the request budget")
        return self


class AnalysisEvidence(Contract):
    evidence_id: str
    product_id: Identifier
    product_title: ShortText
    snapshot_id: Identifier
    document_id: Identifier
    source_url: str
    observed_at: datetime
    kind: Literal["spec", "price", "stock", "review", "title"]
    label: str
    value: str
    review_id: Identifier | None = None
    rating: int | None = None
    verified_purchase_observed: bool | None = None


class AnalysisRow(Contract):
    label: str
    values: dict[str, str | None]
    evidence_ids: dict[str, str | None]


class AnalysisSkill(Contract):
    skill: Literal["highlight_spec", "scroll_to_reviews"]
    product_id: Identifier
    document_id: Identifier
    source_url: str
    spec_key: str | None = None
    review_id: Identifier | None = None


class AnalysisReply(Contract):
    status: Literal["answered", "needs_input", "no_evidence"]
    answer: str
    evidence: tuple[AnalysisEvidence, ...] = ()
    comparison: tuple[AnalysisRow, ...] = ()
    shortlist: tuple[Identifier, ...] = ()
    lowest_observed_price_product_id: Identifier | None = None
    review_sample_size: int = 0
    review_match_count: int = 0
    coverage: str = "Only supplied observations; not all products or all customer reviews."
    warnings: tuple[str, ...] = ()
    follow_up: str | None = None
    skills: tuple[AnalysisSkill, ...] = ()
    processing: Literal["local_extractive", "grounded_model_selection"] = "local_extractive"
    inference_fallback: Literal["deterministic", "cloud"] | None = None


ALIASES = {
    "battery": ("battery", "mah", "बैटरी"),
    "heating": ("heating", "overheat", "overheating", "heat", "heats", "hot", "garam"),
    "weight": ("weight", "vajan", "wajan"),
    "storage": ("storage", "rom"),
    "memory": ("ram", "memory"),
    "display": ("display", "screen"),
    "sound": ("sound", "audio", "speaker"),
    "build": ("build", "durability", "hinge", "headband", "crack"),
    "comfort": ("comfort", "comfortable", "fit"),
}


def normalized(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def spec_key(value):
    key = normalized(value)
    if key in {"ram", "ram size", "ram memory", "installed ram"}:
        return "ram"
    if key in {"storage", "internal storage", "storage capacity", "rom"}:
        return "storage"
    if key in {"color", "colour"}:
        return "colour"
    return key


def spec_value(value):
    # Formatting equivalence only; no GB/MB conversion or inferred quantities.
    return re.sub(r"(?<=\d)\s+(?=(?:gb|mb)\b)", "", normalized(value))


def words_match(text, values):
    return any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in values)


def analyze(request: AnalysisRequest, *, now: datetime, max_age_seconds=120) -> AnalysisReply:
    """No IO, persistence, free-form model claims or browser execution."""
    request = AnalysisRequest.model_validate(request.model_dump())
    question = request.question.casefold()
    hi = request.language == "hi-Latn"

    def text(en, hindi):
        return hindi if hi else en

    fresh, warnings = [], []
    for item in request.products:
        age = (now - item.product.observed_at).total_seconds()
        if 0 <= age <= max_age_seconds:
            fresh.append(item)
        else:
            warnings.append(f"STALE_OBSERVATION:{item.product.product_id}")
    if not fresh:
        return AnalysisReply(
            status="no_evidence",
            warnings=tuple(warnings),
            answer=text(
                "Open a product page and capture fresh product details first.",
                "Product page kholo aur fresh details capture karo; abhi verified facts nahi hain.",
            ),
        )

    evidence, skills, rows = [], [], []

    def cite(item, kind, label, value, *, review=None):
        p = item.product
        entry = AnalysisEvidence(
            evidence_id=f"e{len(evidence) + 1}",
            product_id=p.product_id,
            product_title=p.title,
            snapshot_id=p.snapshot_id,
            document_id=p.document_id,
            source_url=item.source_url,
            observed_at=p.observed_at,
            kind=kind,
            label=label,
            value=value,
            review_id=review.review_id if review else None,
            rating=review.rating if review else None,
            verified_purchase_observed=review.verified_purchase_observed if review else None,
        )
        evidence.append(entry)
        return entry.evidence_id

    topics = [topic for topic, aliases in ALIASES.items() if words_match(question, aliases)]
    review_question = words_match(
        question,
        (
            "review",
            "reviews",
            "complaint",
            "complaints",
            "negative",
            "issue",
            "issues",
            "problem",
            "problems",
            "shikayat",
            "kharab",
            "heating",
            "overheat",
        ),
    )
    compare_question = words_match(
        question, ("compare", "comparison", "versus", "vs", "dono", "tulna")
    )
    recommend = words_match(
        question, ("recommend", "suggest", "best", "kaunsa", "budget", "under", "below")
    )
    if request.selected_product_id and not compare_question and not recommend:
        fresh = [item for item in fresh if item.product.product_id == request.selected_product_id]
        if not fresh:
            return AnalysisReply(
                status="no_evidence",
                answer=text(
                    "Capture fresh details from the selected product page.",
                    "Selected product page ki fresh details capture karo.",
                ),
                warnings=tuple(warnings),
            )
    if (
        recommend
        and not review_question
        and words_match(question, ("not", "nahi", "mat", "avoid", "dont", "don't"))
    ):
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Please confirm the positive budget/specification requirements "
                "before I shortlist products.",
                "Shortlist se pehle budget aur chahiye wali specs clearly confirm karo.",
            ),
        )
    budget = request.budget
    requirements = {spec_key(k): spec_value(v) for k, v in request.required_specs.items()}
    for match in re.finditer(r"(?<!\w)(\d+\s*(?:gb|mb))\s*(ram|storage|rom)\b", question):
        key, value = spec_key(match[2]), spec_value(match[1])
        if key in requirements and requirements[key] != value:
            return AnalysisReply(status="needs_input", answer="Please confirm one value for " + key)
        requirements[key] = value
    for match in re.finditer(r"\b(ram|storage|rom)\s*[:=]?\s*(\d+\s*(?:gb|mb))\b", question):
        key, value = spec_key(match[1]), spec_value(match[2])
        if key in requirements and requirements[key] != value:
            return AnalysisReply(status="needs_input", answer="Please confirm one value for " + key)
        requirements[key] = value
    mentioned = re.findall(
        r"(?<!\w)(?:budget\s*(?:is|hai|:)?|under|below)\s*(?:rs\.?|inr|₹)?\s*"
        r"(\d[\d,]*(?:\.\d{1,2})?)\s*(k|thousand|hazar|lakh|crore)?(?![\w.])",
        question,
    )
    if mentioned:
        from decimal import Decimal

        for value, _ in mentioned:
            if "," in value and not re.fullmatch(
                r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?", value
            ):
                return AnalysisReply(
                    status="needs_input",
                    answer="Please confirm the budget using an unambiguous number.",
                )
        scales = {
            "": 1,
            "k": 1000,
            "thousand": 1000,
            "hazar": 1000,
            "lakh": 100000,
            "crore": 10000000,
        }
        amounts = {Decimal(value.replace(",", "")) * scales[unit] for value, unit in mentioned}
        if len(amounts) != 1 or budget is not None and budget not in amounts:
            return AnalysisReply(
                status="needs_input",
                answer=text(
                    "Please confirm one budget amount and its currency.",
                    "Ek budget amount aur currency confirm karo.",
                ),
            )
        budget = amounts.pop()
    elif (budget is None and words_match(question, ("budget", "under", "below"))) or re.search(
        r"(?:budget|under|below).*\d", question
    ):
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Please confirm your budget amount and currency.",
                "Budget amount aur currency confirm karo.",
            ),
        )
    colours = [
        colour
        for colour in ("black", "blue", "white", "red", "green")
        if words_match(question, (colour,))
    ]
    if len(colours) > 1 or colours and requirements.get("colour", colours[0]) != colours[0]:
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Please confirm one colour requirement.",
                "Ek colour requirement confirm karo.",
            ),
        )
    if colours:
        requirements["colour"] = colours[0]
    if budget is not None and (budget <= 0 or budget > 999999999999):
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Please confirm a positive, supported budget amount.",
                "Ek valid positive budget confirm karo.",
            ),
        )
    if (
        recommend
        and words_match(question, ("gaming", "photography", "travel", "editing"))
        and not requirements
    ):
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Which exact specifications matter for that use case? "
                "I cannot infer suitability from price.",
                "Is use ke liye kaunsi exact specs chahiye? "
                "Sirf price se suitability decide nahi karunga.",
            ),
        )
    if recommend and budget is None and not requirements:
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "What is your budget or required RAM/storage? Best depends on your requirements.",
                "Budget ya required RAM/storage batao. Best tumhari need par depend karta hai.",
            ),
        )
    if compare_question and len(fresh) < 2:
        return AnalysisReply(
            status="needs_input",
            answer=text(
                "Capture a second product to compare them.",
                "Comparison ke liye doosra product bhi capture karo.",
            ),
            warnings=tuple(warnings),
        )

    sample_size, matches = 0, []
    if review_question:
        negative_only = words_match(
            question,
            (
                "complaint",
                "complaints",
                "negative",
                "issue",
                "issues",
                "problem",
                "problems",
                "shikayat",
                "kharab",
                "heating",
                "overheat",
            ),
        )
        for item in fresh:
            seen = set()
            for review in item.reviews:
                fingerprint = normalized(review.text)
                if not fingerprint or fingerprint in seen:
                    continue
                seen.add(fingerprint)
                sample_size += 1
                if negative_only and review.rating > 2:
                    continue
                if topics and not any(
                    words_match(review.text.casefold(), ALIASES[t]) for t in topics
                ):
                    continue
                matches.append((item, review))
        for item, review in matches[:12]:
            # Exact substring of supplied text, including punctuation/case.
            match = next(
                (
                    m
                    for topic in topics
                    for alias in ALIASES[topic]
                    if (
                        m := re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", review.text, re.I)
                    )
                ),
                None,
            )
            start = max(0, match.start() - 80) if match else 0
            cite(
                item,
                "review",
                "Captured review excerpt",
                review.text[start : start + 300],
                review=review,
            )
            skills.append(
                AnalysisSkill(
                    skill="scroll_to_reviews",
                    product_id=item.product.product_id,
                    document_id=item.product.document_id,
                    source_url=item.source_url,
                    review_id=review.review_id,
                )
            )
        return AnalysisReply(
            status="answered" if matches else "no_evidence",
            answer=text(
                f"Found {len(matches)} matching captured reviews in a sample of {sample_size}. "
                "Quotes are excerpts, not independently verified customer claims.",
                f"{sample_size} captured reviews mein {len(matches)} match mile. "
                "Neeche exact excerpts hain; buyers ke claims independently verify nahi hue.",
            ),
            evidence=tuple(evidence),
            review_sample_size=sample_size,
            review_match_count=len(matches),
            warnings=tuple(warnings),
            skills=tuple(skills),
            follow_up=None
            if matches
            else text(
                "Capture more relevant reviews from the page.",
                "Page se relevant reviews capture karo.",
            ),
        )

    if compare_question:
        fields = ["price", "stock"] + sorted({key for item in fresh for key in item.product.specs})
        for key in fields:
            values, ids = {}, {}
            for item in fresh:
                p = item.product
                value = (
                    (f"{p.currency} {p.price:.2f}" if p.price is not None else None)
                    if key == "price"
                    else (
                        "in stock"
                        if p.in_stock
                        else "out of stock"
                        if p.in_stock is False
                        else None
                    )
                    if key == "stock"
                    else p.specs.get(key)
                )
                values[p.product_id] = value
                ids[p.product_id] = (
                    cite(
                        item,
                        "price" if key == "price" else "stock" if key == "stock" else "spec",
                        key,
                        value,
                    )
                    if value is not None
                    else None
                )
            rows.append(AnalysisRow(label=key, values=values, evidence_ids=ids))
    else:
        for item in fresh:
            p = item.product
            if not topics and words_match(
                question, ("price", "cost", "daam", "kitne", "budget", "under", "best")
            ):
                if p.price is not None:
                    cite(item, "price", "price", f"{p.currency} {p.price:.2f}")
            if (
                words_match(question, ("stock", "available", "availability"))
                and p.in_stock is not None
            ):
                cite(item, "stock", "stock", "in stock" if p.in_stock else "out of stock")
            for key, value in p.specs.items():
                relevant = (
                    words_match(question, ("specs", "specifications", "details"))
                    or any(words_match(key.casefold(), ALIASES[t]) for t in topics)
                    or words_match(normalized(question), (normalized(key),))
                )
                if relevant:
                    cite(item, "spec", key, value)
                    skills.append(
                        AnalysisSkill(
                            skill="highlight_spec",
                            product_id=p.product_id,
                            document_id=p.document_id,
                            source_url=item.source_url,
                            spec_key=key,
                        )
                    )

    eligible = []
    if recommend:
        warnings.append("ITEM_PRICE_ONLY_FINAL_TOTAL_UNKNOWN")
        for item in fresh:
            p = item.product
            specs = {}
            for key, value in p.specs.items():
                canonical, observed = spec_key(key), spec_value(value)
                specs[canonical] = (
                    observed if canonical not in specs or specs[canonical] == observed else None
                )
            if p.price is None or p.currency != request.currency or p.in_stock is not True:
                warnings.append(f"PRICE_CURRENCY_OR_STOCK_UNCONFIRMED:{p.product_id}")
                continue
            if budget is not None and p.price > budget:
                continue
            if not all(specs.get(k) == v for k, v in requirements.items()):
                continue
            eligible.append(item)
        eligible.sort(key=lambda item: (item.product.price, item.product.product_id))
        for item in eligible:
            if not any(
                e.product_id == item.product.product_id and e.kind == "price" for e in evidence
            ):
                cite(item, "price", "price", f"{item.product.currency} {item.product.price:.2f}")
    if len({item.product.currency for item in fresh if item.product.price is not None}) > 1:
        warnings.append("MIXED_CURRENCIES_NO_CONVERSION")
    if not evidence:
        return AnalysisReply(
            status="no_evidence",
            warnings=tuple(warnings),
            answer=text(
                "That detail is not present in the captured evidence. I cannot infer it.",
                "Yeh detail captured evidence mein nahi hai; main guess nahi karunga.",
            ),
            follow_up=text(
                "Ask about an observed specification or capture more details.",
                "Observed spec puchho ya aur details capture karo.",
            ),
        )
    if recommend:
        answer = text(
            f"{len(eligible)} captured products meet the checked price, stock "
            "and exact-spec constraints. "
            "The shortlist is ordered by observed price, not overall product quality.",
            f"{len(eligible)} captured products checked price, stock aur exact specs se match hue. "
            "Shortlist observed price ke order mein hai; overall quality ka claim nahi hai.",
        )
    elif compare_question:
        answer = text(
            "Compared the captured facts. Missing values remain unknown.",
            "Captured facts compare kiye hain. Missing values unknown hi hain.",
        )
    else:
        answer = text(
            "These details come directly from the captured product evidence.",
            "Yeh details seedhe captured product evidence se hain.",
        )
    return AnalysisReply(
        status="answered",
        answer=answer,
        evidence=tuple(evidence),
        comparison=tuple(rows),
        shortlist=tuple(item.product.product_id for item in eligible),
        lowest_observed_price_product_id=eligible[0].product.product_id if eligible else None,
        warnings=tuple(warnings),
        skills=tuple(skills),
    )


def selected_fact_reply(
    request: AnalysisRequest, response: AIResponse, *, now: datetime, max_age_seconds=120
) -> AnalysisReply | None:
    """A model may select fresh fact IDs; all displayed values/targets stay server-rendered."""
    from shopping_ai.context import product_facts

    if response.status != "answered" or not response.facts:
        return None
    selected = next(
        (
            item
            for item in request.products
            if item.product.product_id == request.selected_product_id
        ),
        None,
    )
    if selected is None and len(request.products) == 1:
        selected = request.products[0]
    if (
        selected is None
        or not 0 <= (now - selected.product.observed_at).total_seconds() <= max_age_seconds
    ):
        return None
    p = selected.product
    allowed = {fact.fact_id: fact for fact in product_facts(p)}
    if len({fact.fact_id for fact in response.facts}) != len(response.facts):
        return None
    if any(allowed.get(fact.fact_id) != fact for fact in response.facts):
        return None
    evidence, skills = [], []
    for fact in response.facts:
        key, value = fact.key, fact.value
        kind = "spec"
        if key.startswith("spec:"):
            key, value = list(p.specs.items())[int(key.split(":")[1])]
            skills.append(
                AnalysisSkill(
                    skill="highlight_spec",
                    product_id=p.product_id,
                    document_id=p.document_id,
                    source_url=selected.source_url,
                    spec_key=key,
                )
            )
        elif key in {"price", "stock", "title"}:
            kind = key
        evidence.append(
            AnalysisEvidence(
                evidence_id=fact.fact_id,
                product_id=p.product_id,
                product_title=p.title,
                snapshot_id=p.snapshot_id,
                document_id=p.document_id,
                source_url=selected.source_url,
                observed_at=p.observed_at,
                kind=kind,
                label=key,
                value=value,
            )
        )
    return AnalysisReply(
        status="answered",
        processing="grounded_model_selection",
        answer="Related captured facts selected for your question. Check their labels and sources."
        if request.language == "en"
        else "Tumhare sawal se related captured facts yeh hain. Labels aur sources check karo.",
        evidence=tuple(evidence),
        skills=tuple(skills),
    )
