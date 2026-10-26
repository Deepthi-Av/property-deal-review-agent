"""Author small synthetic evaluation packets before prompt tuning; no network."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
original = json.loads((ROOT / "data/examples.json").read_text(encoding="utf-8"))
base = original[0]
records = []

def record(key, title, description, source_text, documents, expected, answers=None, fault=None):
    case = copy.deepcopy(base)
    case.pop("expected_facts")
    case.update(case_id=key, title=title, description=description)
    case["current"]["source_text"] = source_text
    case["current"]["documents"] = documents
    records.append({"case": case, "expected": expected, "reviewer_answers": answers or {}, "tool_fault": fault})

text = base["current"]["source_text"]
record("evidence_complete", "Evidence: complete packet", "A complete control: no question or research is needed.", text, [],
       {"initial_unresolved": [], "final_monthly_total": "3220.00", "required_issues": [], "required_sources": []})

missing = text.replace("Homeowners insurance: USD 1,800.00 per year.\n", "")
record("evidence_elsewhere", "Evidence: insurance in correspondence", "The worksheet omits a cost that is explicitly stated in another supplied source.", missing,
       [{"source_id": "insurance-email", "title": "Insurance cost confirmation", "as_of": "2026-10-23",
         "text": "SYNTHETIC correspondence. Homeowners insurance for this purchase is USD 165.00 per month. This is the quoted recurring charge for the same property; it is separate from mortgage insurance."}],
       {"initial_unresolved": [], "final_monthly_total": "3235.00", "required_issues": [], "required_sources": ["current:insurance-email"]})

record("evidence_conflict", "Evidence: an unapproved HOA change", "A later email mentions a different figure but does not establish a replacement charge.", text,
       [{"source_id": "hoa-email", "title": "Association discussion about revised dues", "as_of": "2026-10-24",
         "text": "SYNTHETIC email. HOA fee: USD 190.00 per month. This is an amount under discussion; the association has not voted on it. The worksheet still uses the earlier charge. The date of this email does not establish an agreed replacement."}],
       {"initial_unresolved": ["hoa"], "final_monthly_total": "3245.00", "required_issues": [["hoa", "provisional"]], "required_sources": ["current:hoa-email"]}, {"hoa": "175"})

zero = text.replace("Homeowners insurance: USD 1,800.00 per year.", "Homeowners insurance: USD 0.00 per month.")
record("evidence_placeholder", "Evidence: zero awaiting a broker response", "A numeric entry has a contextual qualification. A source read fails once in the evaluation.", zero,
       [{"source_id": "broker-note", "title": "Explanation of the insurance worksheet entry", "as_of": "2026-10-24",
         "text": "SYNTHETIC reviewer note about homeowners insurance. We entered zero to keep the worksheet calculations working while we wait for the broker. It is not a price the broker supplied. There is no insurance quote anywhere in this packet."}],
       {"initial_unresolved": ["home_insurance"], "final_monthly_total": None, "required_issues": [["home_insurance", "provisional"]], "required_sources": ["current:broker-note"]},
       {"home_insurance": None}, {"source_id": "current:broker-note", "failures": 1})

destination = ROOT / "data/investigation-evaluation.json"
if destination.exists():
    raise SystemExit("Evaluation packets already exist; refusing to overwrite frozen cases.")
payload = json.dumps(records, indent=2, ensure_ascii=False) + "\n"
destination.write_text(payload, encoding="utf-8")
print("Frozen cases:", len(records))
print("SHA256:", hashlib.sha256(payload.encode()).hexdigest())
