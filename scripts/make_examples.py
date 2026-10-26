"""Create the small, explicitly synthetic evaluation collection."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    "purchase_price": ("Purchase price", "once"),
    "principal_interest": ("Principal and interest", "monthly"),
    "property_tax": ("Property tax", "annual"),
    "home_insurance": ("Homeowners insurance", "annual"),
    "mortgage_insurance": ("Mortgage insurance", "monthly"),
    "hoa": ("HOA fee", "monthly"),
    "cash_to_close": ("Estimated cash to close", "once"),
}


def packet(deal_id: str, name: str, amounts: dict, *, conflict: bool = False) -> tuple[dict, list]:
    lines = [f"SYNTHETIC example: {name}. All figures USD. Fixed-rate financing.",
             "Payment components below are separate; do not add an escrow total."]
    facts = []
    for field, amount in amounts.items():
        label, period = SPECS[field]
        unit = {"once": "one-time", "monthly": "per month", "annual": "per year"}[period]
        quote = f"{label}: USD {amount:,.2f} {unit}."
        lines.append(quote)
        facts.append({"field": field, "amount": str(amount), "period": period, "quote": quote, "provenance": "document"})
    if conflict:
        quote = "Lender worksheet HOA fee: USD 190.00 per month."
        lines.append(quote)
        facts.append({"field": "hoa", "amount": "190", "period": "monthly", "quote": quote, "provenance": "document"})
    return ({"deal_id": deal_id, "name": name, "as_of": "2026-10-23" if deal_id == "new" else "2026-10-20",
             "source_text": "\n".join(lines)}, facts)


def main() -> None:
    current = dict(purchase_price=420000, principal_interest=2300, property_tax=6000,
                   home_insurance=1800, mortgage_insurance=120, hoa=150, cash_to_close=94000)
    saved = dict(purchase_price=410000, principal_interest=2250, property_tax=4800,
                 home_insurance=1500, mortgage_insurance=0, hoa=125, cash_to_close=85000)
    variants = [
        ("clean", "Complete figures", "New deal totals $3,220/month and $94,000 cash to close.", current, False),
        ("missing", "Missing insurance", "Insurance is absent, so the monthly total must remain unknown until confirmed.", {k: v for k, v in current.items() if k != "home_insurance"}, False),
        ("conflict", "Conflicting HOA amounts", "Two source lines disagree. A reviewer must confirm the monthly HOA amount.", current, True),
        ("over_limit", "Over the monthly limit", "Known monthly costs exceed the default buyer limit.", {**current, "principal_interest": 3000}, False),
    ]
    cases = []
    for case_id, title, description, amounts, conflict in variants:
        new_deal, new_facts = packet("new", "New purchase", amounts, conflict=conflict)
        saved_deal, saved_facts = packet("saved", "Previously reviewed deal", saved)
        cases.append({"case_id": case_id, "title": title, "description": description,
                      "is_synthetic": True, "current": new_deal, "saved": saved_deal,
                      "expected_facts": {"current": new_facts, "saved": saved_facts}})
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "examples.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} synthetic examples; expected labels are excluded from live prompts.")


if __name__ == "__main__":
    main()

