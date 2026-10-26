"""Deterministic evidence validation, Decimal calculations and report facts."""

import re
from decimal import ROUND_HALF_UP, Decimal

from home_review.models import Deal, Fact, Finding, LABELS, Limits, MONTHLY_FIELDS

CENT = Decimal("0.01")
NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?!\w|\.\d)")
FIELD_WORDS = {
    "purchase_price": ("purchase price",),
    "principal_interest": ("principal and interest", "principal & interest"),
    "property_tax": ("property tax",),
    "home_insurance": ("homeowners insurance", "home insurance"),
    "mortgage_insurance": ("mortgage insurance",),
    "hoa": ("hoa", "homeowners association"),
    "cash_to_close": ("cash to close",),
}


def money(value: Decimal) -> str:
    return f"${value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def validate_facts(deal: Deal, facts: list[Fact]) -> tuple[list[Fact], list[str]]:
    """Reject unsupported quotes, amounts, units and model-supplied reviewer provenance."""
    valid, errors = [], []
    source = normalize_space(deal.source_text)
    for fact in facts:
        quote = normalize_space(fact.quote)
        reason = None
        if fact.provenance != "document":
            reason = "model cannot create reviewer evidence"
        elif quote not in source:
            reason = "quoted passage is absent from the source"
        elif fact.amount not in {Decimal(n.replace(",", "")) for n in NUMBER.findall(quote)}:
            reason = "amount is absent from the quoted passage"
        elif not any(word in quote.lower() for word in FIELD_WORDS[fact.field]):
            reason = "field label is absent from the quoted passage"
        elif fact.field in MONTHLY_FIELDS:
            lower = quote.lower()
            if fact.period == "monthly" and not any(x in lower for x in ("monthly", "per month", "/month")):
                reason = "monthly unit is not explicit in the passage"
            elif fact.period == "annual" and not any(x in lower for x in ("annual", "per year", "/year")):
                reason = "annual unit is not explicit in the passage"
            elif fact.period == "once":
                reason = "recurring component needs a monthly or annual unit"
        elif fact.period != "once":
            reason = "purchase price and cash to close must be one-time amounts"
        if reason:
            errors.append(f"{deal.name}: rejected {LABELS[fact.field]} ({reason}).")
        else:
            valid.append(fact)
    return valid, errors


def normalized_amount(fact: Fact) -> Decimal:
    """Do not round individual components before adding them."""
    return fact.amount / 12 if fact.period == "annual" else fact.amount


def audit_source_coverage(deal: Deal, facts: list[Fact]) -> tuple[list[Fact], list[str]]:
    """Recover omitted explicitly labeled amounts in the PREPARED input format.

    This is not a parser for arbitrary loan documents. It independently examines
    short, single-field USD lines so a model cannot silently erase a second quote.
    It never uses expected labels or chooses between conflicting figures.
    """
    audited, warnings = list(facts), []
    seen = {(f.field, f.amount, f.period, normalize_space(f.quote)) for f in facts}
    # Also accept short explicit sentences inside a prepared correspondence paragraph.
    # Decimal points are not followed by a space and an uppercase letter.
    for line in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\r?\n", deal.source_text):
        lower = line.lower()
        fields = [field for field, words in FIELD_WORDS.items() if any(word in lower for word in words)]
        numbers = NUMBER.findall(line)
        if len(fields) != 1 or len(numbers) != 1 or "usd" not in lower:
            continue
        field = fields[0]
        if field in MONTHLY_FIELDS:
            if any(word in lower for word in ("monthly", "per month", "/month")):
                period = "monthly"
            elif any(word in lower for word in ("annual", "per year", "/year")):
                period = "annual"
            else:
                continue
        else:
            period = "once"
        candidate = Fact(field=field, amount=Decimal(numbers[0].replace(",", "")), period=period, quote=line)
        signature = (candidate.field, candidate.amount, candidate.period, normalize_space(candidate.quote))
        # A model may quote a containing passage, rather than this exact line.
        already_covered = any(f.field == candidate.field and f.amount == candidate.amount and f.period == candidate.period
                              and normalize_space(line) in normalize_space(f.quote) for f in facts)
        if signature not in seen and not already_covered:
            audited.append(candidate)
            seen.add(signature)
            warnings.append(f"{deal.name}: source-format audit recovered an omitted {LABELS[field]} figure. Check the source evidence; conflicting figures still require confirmation.")
    return audited, warnings


def summarize_deal(deal: Deal, facts: list[Fact], limits: Limits, prefix: str) -> dict:
    rows, unresolved, citations, findings = {}, [], {}, []
    for index, fact in enumerate(facts):
        document = next((d for d in deal.documents if d.source_id == fact.source_id), None)
        citations[f"{prefix}{index + 1}"] = {
            "deal": deal.name, "field": LABELS[fact.field], "quote": fact.quote,
            "as_of": "current review session" if fact.provenance == "reviewer" else str((document.as_of if document else deal.as_of) or "Not provided"),
            "provenance": fact.provenance,
            "source_id": fact.source_id,
            "source_title": "Reviewer confirmation" if fact.provenance == "reviewer" else (document.title if document else deal.name),
            "document_type": "Reviewer confirmation" if fact.provenance == "reviewer" else (document.document_type if document else deal.document_type),
        }
    for field in LABELS:
        matching = [(index, f) for index, f in enumerate(facts) if f.field == field]
        values = {normalized_amount(f) for _, f in matching}
        ids = [f"{prefix}{i + 1}" for i, _ in matching]
        if len(values) == 1:
            amount = next(iter(values))
            rows[field] = {"value": str(amount), "evidence_ids": ids, "state": "known"}
        else:
            state = "missing" if not values else "conflicting"
            rows[field] = {"value": None, "evidence_ids": ids, "state": state}
            unresolved.append(field)
            findings.append(Finding(
                finding_id=f"{prefix}_{field}_{state}",
                message=f"{deal.name}: {LABELS[field]} is {state}; confirmation is needed.",
                evidence_ids=ids,
            ))
    subtotal = sum((Decimal(rows[f]["value"]) for f in MONTHLY_FIELDS if rows[f]["value"] is not None), Decimal(0))
    complete = all(rows[f]["value"] is not None for f in MONTHLY_FIELDS)
    total = subtotal.quantize(CENT, rounding=ROUND_HALF_UP) if complete else None
    cash = Decimal(rows["cash_to_close"]["value"]) if rows["cash_to_close"]["value"] is not None else None
    # Unknown expenses cannot turn an over-limit known subtotal into a pass.
    monthly_status = "fail" if subtotal > limits.monthly else ("pass" if complete else "unknown")
    cash_status = "unknown" if cash is None else ("pass" if cash <= limits.cash else "fail")
    for check, status, value, evidence in (
        ("monthly budget", monthly_status, total, [e for f in MONTHLY_FIELDS for e in rows[f]["evidence_ids"]]),
        ("cash-to-close limit", cash_status, cash, rows["cash_to_close"]["evidence_ids"]),
    ):
        detail = money(value) if value is not None else "incomplete information"
        findings.append(Finding(
            finding_id=f"{prefix}_{check.replace(' ', '_')}",
            message=f"{deal.name}: {check} = {status.upper()} ({detail}).",
            evidence_ids=evidence,
        ))
    return {
        "name": deal.name, "as_of": str(deal.as_of or "Not provided"), "rows": rows,
        "source_kind": deal.source_kind, "document_type": deal.document_type,
        "known_subtotal": str(subtotal.quantize(CENT, rounding=ROUND_HALF_UP)),
        "monthly_total": str(total) if total is not None else None,
        "cash_to_close": str(cash) if cash is not None else None,
        "monthly_status": monthly_status, "cash_status": cash_status,
        "unresolved": unresolved, "citations": citations,
        "findings": [f.model_dump() for f in findings],
    }


def compare_deals(current: dict, saved: dict) -> list[dict]:
    findings = []
    for key, label in (("monthly_total", "monthly housing cost"), ("cash_to_close", "cash to close")):
        if current[key] is None or saved[key] is None:
            text = f"Cannot calculate the {label} difference because a required figure is unresolved."
        else:
            delta = Decimal(current[key]) - Decimal(saved[key])
            direction = "higher" if delta > 0 else "lower" if delta < 0 else "the same"
            text = (f"New deal {label} is {money(abs(delta))} {direction} than the saved deal."
                    if delta else f"New and saved deal {label} are the same.")
        findings.append(Finding(
            finding_id=f"compare_{key}", message=text,
            evidence_ids=list(current["citations"]) + list(saved["citations"]),
        ).model_dump())
    findings.append(Finding(
        finding_id="comparison_dates",
        message=f"Compare source dates: new {current['as_of']}; saved {saved['as_of']}. These figures are not current market valuations.",
        evidence_ids=[],
    ).model_dump())
    return findings


def apply_clarifications(facts: list[Fact], values: dict, allowed_fields: list[str]) -> list[Fact]:
    """Replace only unresolved facts. None means the reviewer leaves an item unknown."""
    revised = list(facts)
    for field, value in values.items():
        if field not in allowed_fields:
            raise ValueError("Only unresolved current-deal fields can be confirmed.")
        if value is None or value == "":
            continue
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 100_000_000:
            raise ValueError("Confirmed amounts must be finite, non-negative numbers within the supported range.")
        period = "monthly" if field in MONTHLY_FIELDS else "once"
        fact = Fact(field=field, amount=amount, period=period, provenance="reviewer",
                    quote=f"Reviewer confirmed {LABELS[field]}: {money(amount)} ({period}).")
        revised = [f for f in revised if f.field != field] + [fact]
    return revised

