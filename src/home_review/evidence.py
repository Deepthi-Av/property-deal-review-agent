"""Read-only packet tools and common evidence guards for ALL evaluation approaches."""
import re
from decimal import Decimal

from home_review.checks import (FIELD_WORDS, NUMBER, audit_source_coverage, compare_deals,
                                normalize_space, summarize_deal, validate_facts)
from home_review.models import ContextIssue, Deal, DemoCase, Fact, Limits, SourcedFact


def packet_sources(case: DemoCase) -> dict[str, dict]:
    sources = {}
    for label in ("current", "saved"):
        deal = getattr(case, label)
        sources[f"{label}:primary"] = {"deal": label, "source_id": "primary", "title": deal.name,
            "document_type": deal.document_type, "as_of": str(deal.as_of or "Not provided"), "text": deal.source_text}
        for doc in deal.documents:
            key = f"{label}:{doc.source_id}"
            if key in sources or doc.source_id in ("primary", "reviewer"):
                raise ValueError("Source identifiers must be unique within a deal; primary and reviewer are reserved.")
            sources[key] = {"deal": label, **doc.model_dump(mode="json")}
    return sources


class LocalSourceTool:
    """Sources are selected by ID, never by a model-supplied filesystem path."""
    def __init__(self, transient_failures: dict[str, int] | None = None):
        self.failures = dict(transient_failures or {})

    def read(self, sources: dict, source_id: str) -> dict:
        if source_id not in sources:
            return {"error": "Unknown source ID; no file was opened."}
        if self.failures.get(source_id, 0) > 0:
            self.failures[source_id] -= 1
            return {"error": "Simulated transient source-read failure; a retry is allowed."}
        return {"source": sources[source_id]}

    def search(self, sources: dict, query: str) -> dict:
        terms = set(re.findall(r"[a-z]{3,}", query.lower()))
        hits = []
        for key, source in sources.items():
            words = set(re.findall(r"[a-z]{3,}", (source["title"] + " " + source["text"]).lower()))
            score = len(words & terms)
            if score:
                hits.append({"source_id": key, "title": source["title"], "matches": score})
        return {"hits": sorted(hits, key=lambda h: (-h["matches"], h["source_id"]))[:6]}


def accept_facts(case: DemoCase, proposals: list[SourcedFact], read_sources: dict,
                 existing: dict | None = None, audit: bool = True) -> tuple[dict, list[str]]:
    accepted = {label: [Fact.model_validate(f) for f in (existing or {}).get(label, [])] for label in ("current", "saved")}
    errors = []
    for item in proposals:
        key = f"{item.deal}:{item.fact.source_id}"
        if item.fact.source_id == "reviewer":
            errors.append("Rejected model relabeling of reviewer evidence as a document fact.")
            continue
        if key not in read_sources:
            errors.append(f"Rejected a fact citing unread or unknown source {key}.")
            continue
        source = read_sources[key]
        deal = getattr(case, item.deal).model_copy(update={"source_text": source["text"]})
        valid, rejected = validate_facts(deal, [item.fact])
        accepted[item.deal].extend(valid)
        errors.extend(rejected)
    if audit:
        for key, source in read_sources.items():
            deal = getattr(case, source["deal"]).model_copy(update={"source_text": source["text"]})
            local = [f for f in accepted[source["deal"]] if f.source_id == source["source_id"]]
            recovered, warnings = audit_source_coverage(deal, local)
            accepted[source["deal"]].extend(f.model_copy(update={"source_id": source["source_id"]}) for f in recovered)
            errors.extend(warnings)
    result = {}
    for label, facts in accepted.items():
        unique = {(f.field, str(f.amount), f.period, normalize_space(f.quote), f.source_id, f.provenance): f for f in facts}
        result[label] = [f.model_dump(mode="json") for f in unique.values()]
    return result, list(dict.fromkeys(errors))


def accept_issues(proposals: list[ContextIssue], read_sources: dict, existing: list | None = None) -> tuple[list, list[str]]:
    issues, errors = list(existing or []), []
    for issue in proposals:
        key = f"{issue.deal}:{issue.source_id}"
        source = read_sources.get(key)
        if not source or normalize_space(issue.quote) not in normalize_space(source["text"]):
            errors.append("Rejected a contextual issue without a quotation in a read source.")
            continue
        # A stated amount is not evidence that the amount is provisional. This
        # shared guard was added after the FIRST live run exposed overblocking.
        # It does not assert semantic entailment for other accepted passages.
        amount_only = re.fullmatch(
            r"[A-Za-z &-]+:\s*USD\s*[\d,.]+\s*(?:per month|per year|monthly|annual|one-time)\.?",
            normalize_space(issue.quote), flags=re.IGNORECASE)
        if amount_only:
            errors.append("Rejected a contextual issue supported only by an ordinary amount statement.")
            continue
        # English-only scope gate: the QUOTE itself needs a qualification, not
        # a qualification invented in the explanation. This is an eligibility
        # heuristic, not an entailment proof or a general document classifier.
        qualifier = re.search(
            r"\b(not|no|never|unapproved|placeholder|provisional|pending|awaiting|wait|waiting|"
            r"if|unless|until|draft|proposed|conditional|excludes?|excluded|subject to|under discussion)\b",
            issue.quote, flags=re.IGNORECASE)
        if not qualifier:
            errors.append("Rejected a contextual issue whose quotation contains no supported qualification language.")
            continue
        # This validates traceability, not whether the model's interpretation is true.
        issues.append(issue.model_dump())
    unique = {}
    for issue in issues:
        key = (issue["deal"], issue["field"], issue["kind"], issue["source_id"], normalize_space(issue["quote"]))
        # Preserve the original interpretation; later model calls must not rewrite
        # document history to incorporate a reviewer-supplied number.
        unique.setdefault(key, issue)
    return list(unique.values()), errors


def calculate(case: DemoCase, facts: dict, issues: list, limits: Limits, confirmed: list[str] | None = None) -> tuple[dict, list]:
    checks = {}
    confirmed = confirmed or []
    for label, prefix in (("current", "N"), ("saved", "S")):
        blocked = {i["field"] for i in issues if i["deal"] == label and i["needs_confirmation"]
                   and not (label == "current" and i["field"] in confirmed)}
        accepted = [Fact.model_validate(f) for f in facts.get(label, []) if f["field"] not in blocked
                    and not (label == "current" and f["field"] in confirmed and f["provenance"] != "reviewer")]
        check = summarize_deal(getattr(case, label), accepted, limits, prefix)
        for field in blocked:
            check["rows"][field]["state"] = "unconfirmed"
            for finding in check["findings"]:
                if finding["finding_id"] == f"{prefix}_{field}_missing":
                    finding["message"] = f"{getattr(case, label).name}: {field.replace('_', ' ')} needs confirmation because of its source context."
        checks[label] = check
    return checks, checks["current"]["findings"] + checks["saved"]["findings"] + compare_deals(checks["current"], checks["saved"])


def rules_issues(sources: dict) -> list[dict]:
    """Documented, generic lexical baseline; it reads the SAME complete sources.

    Detects common qualification phrases near explicit component names. No case IDs
    or expected outcomes. Deliberately retains the interpretation's source passage.
    """
    issues = []
    qualifiers = ("placeholder", "not approved", "not confirmed", "not final", "under discussion",
                  "awaiting", "pending", "not a quote", "not voted", "provisional")
    for key, source in sources.items():
        lower = source["text"].lower()
        if not any(q in lower for q in qualifiers):
            continue
        fields = [field for field, aliases in FIELD_WORDS.items() if any(a in lower for a in aliases)]
        for field in fields:
            issues.append(ContextIssue(deal=source["deal"], field=field, kind="provisional",
                source_id=source["source_id"], quote=source["text"][:1200],
                explanation="A generic qualifier pattern indicates this component needs confirmation.",
                needs_confirmation=True).model_dump())
    return issues


def accept_statements(statements: list, sources: dict, checks: dict, limits: dict,
                      issues: list | None = None, questions: bool = False) -> tuple[list, list[str]]:
    accepted, errors = [], []
    for statement in statements:
        item = statement.model_dump() if hasattr(statement, "model_dump") else statement
        if not item["source_refs"] or any(ref not in sources for ref in item["source_refs"]):
            errors.append("Rejected briefing text with an unread or unknown source reference.")
            continue
        # Scope generated narrative to a cited, accepted context issue. Numeric
        # comparisons already have deterministic wording in the main report.
        if issues is not None:
            relevant = [issue for issue in issues if f"{issue['deal']}:{issue['source_id']}" in item["source_refs"]]
            if questions:
                relevant = [issue for issue in relevant if issue["field"] in checks[issue["deal"]]["unresolved"]]
            if not relevant:
                errors.append("Omitted draft narrative without an accepted contextual issue in scope.")
                continue
        # A calculated or reviewer-supplied number cannot be attributed to a
        # document merely because it appears elsewhere in the state.
        evidence = " ".join(sources[ref]["text"] for ref in item["source_refs"])
        supported = {Decimal(n.replace(",", "")) for n in NUMBER.findall(evidence)}
        proposed = {Decimal(n.replace(",", "")) for n in NUMBER.findall(item["text"])}
        if not proposed <= supported:
            errors.append("Rejected briefing text containing an unsupported number.")
            continue
        accepted.append(item)
    return accepted, errors
