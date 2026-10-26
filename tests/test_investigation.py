"""Behavioral regressions for evidence, routing and baseline fairness; no network."""
from copy import deepcopy

import pytest

from home_review.baselines import rules_review, single_call_review
from home_review.checks import audit_source_coverage
from home_review.evidence import LocalSourceTool, accept_facts, accept_issues, accept_statements, calculate, packet_sources
from home_review.fixtures import load_investigation_cases
from home_review.models import (ContextIssue, Decision, Extraction, InvestigationPlan, Limits,
                                RiskBrief, SingleCallReview, SourcedFact)
from home_review.workflow import ReviewService
from conftest import FakeSearch

LIMITS = Limits(monthly="3300", cash="95000")


class EvidenceModel:
    """Returns explicit test outputs; does not read evaluation gold or case IDs."""
    def __init__(self, *, skip_investigation=False, finalize_early=False):
        self.calls = []
        self.skip_investigation = skip_investigation
        self.finalize_early = finalize_early

    def complete(self, role, schema, payload):
        self.calls.append((role, deepcopy(payload)))
        if role == "extraction":
            from home_review.models import Deal
            deal = Deal(deal_id="test", name="Test", as_of="2026-10-24", source_text=payload["source_text"])
            facts, _ = audit_source_coverage(deal, [])
            return Extraction(facts=facts)
        if role in ("investigation", "risk_review"):
            issues = []
            if role != "investigation" or not self.skip_investigation:
                for source in payload["read_sources"].values():
                    if "wait for the broker" in source["text"]:
                        issues.append(ContextIssue(deal=source["deal"], field="home_insurance", kind="provisional",
                            source_id=source["source_id"], quote=source["text"],
                            explanation="The worksheet entry is not a broker-supplied price.", needs_confirmation=True))
            return InvestigationPlan(issues=issues) if role == "investigation" else RiskBrief(issues=issues)
        if role == "orchestrator":
            if "read_source" in payload["allowed_actions"] and not self.finalize_early:
                return Decision(action="read_source", source_id=next(iter(payload["unread_sources"])), reason="Read the available evidence.")
            return Decision(action="finalize", reason="Test early finish request.")
        raise AssertionError(role)


@pytest.fixture
def evidence_cases():
    return {c.case_id: c for c in load_investigation_cases()}


def test_source_read_resolves_missing_amount_without_human(evidence_cases):
    model = EvidenceModel()
    service = ReviewService(model, FakeSearch())
    snap = service.snapshot(service.start(evidence_cases["evidence_elsewhere"], LIMITS))
    assert snap["pending"] is None
    assert snap["state"]["report"]["checks"]["current"]["monthly_total"] == "3235.00"
    assert snap["state"]["report"]["coverage_complete"]
    assert snap["state"]["local_count"] == 1
    assert "expected" not in str(model.calls)
    coordinator = next(payload for role, payload in model.calls if role == "orchestrator")
    assert "USD 165" not in str(coordinator["unread_sources"])


def test_context_blocks_placeholder_and_handoff_is_mandatory(evidence_cases):
    model = EvidenceModel(finalize_early=True)
    service = ReviewService(model, FakeSearch())
    review_id = service.start(evidence_cases["evidence_placeholder"], LIMITS)
    snap = service.snapshot(review_id)
    assert snap["pending"]["fields"] == ["home_insurance"]
    assert snap["state"]["checks"]["current"]["monthly_total"] is None
    service.resume(review_id, {"home_insurance": "160"})
    report = service.snapshot(review_id)["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3230.00"
    assert report["confirmed"] == ["home_insurance"]
    assert "current:reviewer" in report["read_sources"]
    assert not any("audit recovered" in error for error in report["errors"])
    reviewer_citations = [c for c in report["checks"]["current"]["citations"].values() if c["provenance"] == "reviewer"]
    assert reviewer_citations and all(c["as_of"] == "current review session" for c in reviewer_citations)
    assert sum(role == "extraction" for role, _ in model.calls) == 2


def test_risk_review_new_issue_returns_to_human_gate(evidence_cases):
    model = EvidenceModel(skip_investigation=True)
    service = ReviewService(model, FakeSearch())
    review_id = service.start(evidence_cases["evidence_placeholder"], LIMITS)
    snap = service.snapshot(review_id)
    assert snap["pending"]["fields"] == ["home_insurance"]
    assert snap["state"]["contributions"]["risk_issues"] == 1
    service.resume(review_id, {"home_insurance": None})
    final = service.snapshot(review_id)
    assert final["pending"] is None
    assert final["state"]["report"]["checks"]["current"]["monthly_total"] is None


def test_transient_read_failure_recovers_and_preserves_error(evidence_cases):
    service = ReviewService(EvidenceModel(), FakeSearch(), LocalSourceTool({"current:broker-note": 1}))
    snap = service.snapshot(service.start(evidence_cases["evidence_placeholder"], LIMITS))
    assert snap["pending"]["fields"] == ["home_insurance"]
    assert snap["state"]["read_attempts"]["current:broker-note"] == 2
    assert any("Simulated transient" in error for error in snap["state"]["errors"])


def test_permanent_read_failure_is_bounded_and_report_is_incomplete(evidence_cases):
    service = ReviewService(EvidenceModel(), FakeSearch(), LocalSourceTool({"current:insurance-email": 10}))
    review_id = service.start(evidence_cases["evidence_elsewhere"], LIMITS)
    assert service.snapshot(review_id)["pending"]
    service.resume(review_id, {"home_insurance": None})
    report = service.snapshot(review_id)["state"]["report"]
    assert report["local_count"] == 2
    assert not report["coverage_complete"]
    assert report["checks"]["current"]["monthly_total"] is None


def test_unread_source_cannot_supply_a_fact_or_issue(evidence_cases):
    case = evidence_cases["evidence_elsewhere"]
    sources = packet_sources(case)
    primary = {k: v for k, v in sources.items() if v["source_id"] == "primary"}
    from home_review.models import Fact
    proposal = SourcedFact(deal="current", fact=Fact(field="home_insurance", amount="165", period="monthly",
        source_id="insurance-email", quote="Homeowners insurance for this purchase is USD 165.00 per month."))
    facts, errors = accept_facts(case, [proposal], primary, audit=False)
    assert facts["current"] == [] and errors
    issues, errors = accept_issues([ContextIssue(deal="current", field="home_insurance", kind="provisional",
        source_id="insurance-email", quote="Homeowners insurance for this purchase is USD 165.00 per month.",
        explanation="Unsupported", needs_confirmation=True)], primary)
    assert not issues and errors


def test_calculated_or_reviewer_number_needs_its_own_cited_evidence(evidence_cases):
    from home_review.models import CitedStatement
    sources = packet_sources(evidence_cases["evidence_conflict"])
    statement = CitedStatement(text="The worksheet states an HOA fee of 175.00.", source_refs=["current:primary"])
    accepted, errors = accept_statements([statement], sources, {"hoa": "175.00"}, {})
    assert not accepted and errors
    sources["current:reviewer"] = {"text": "Reviewer confirmed HOA fee: $175.00 (monthly)."}
    proper = CitedStatement(text="The reviewer supplied 175.00.", source_refs=["current:reviewer"])
    accepted, errors = accept_statements([proper], sources, {}, {})
    assert len(accepted) == 1 and not errors


def test_later_interpretation_cannot_rewrite_original_issue(evidence_cases):
    sources = packet_sources(evidence_cases["evidence_conflict"])
    original = ContextIssue(deal="current", field="hoa", kind="provisional", source_id="hoa-email",
        quote=sources["current:hoa-email"]["text"], explanation="The source describes an unapproved charge.", needs_confirmation=True)
    issues, _ = accept_issues([original], sources)
    rewritten = original.model_copy(update={"explanation": "The source now says the reviewer's new amount."})
    combined, _ = accept_issues([rewritten], sources, issues)
    assert combined[0]["explanation"] == original.explanation


def test_model_cannot_relabel_reviewer_evidence_as_document(evidence_cases):
    from home_review.models import Fact
    case = evidence_cases["evidence_complete"]
    sources = packet_sources(case)
    sources["current:reviewer"] = {"deal": "current", "source_id": "reviewer", "text": "Reviewer confirmed HOA fee: $175.00 (monthly)."}
    item = SourcedFact(deal="current", fact=Fact(field="hoa", amount="175", period="monthly",
        source_id="reviewer", quote=sources["current:reviewer"]["text"]))
    facts, errors = accept_facts(case, [item], sources, audit=False)
    assert facts["current"] == [] and errors


def test_tool_cannot_open_arbitrary_paths(evidence_cases):
    outcome = LocalSourceTool().read(packet_sources(evidence_cases["evidence_complete"]), "../../.env")
    assert "error" in outcome and "source" not in outcome


@pytest.mark.parametrize("field, quote", [
    ("property_tax", "Property tax: USD 6,000.00 per year."),
    ("cash_to_close", "Estimated cash to close: USD 94,000.00 one-time."),
    ("mortgage_insurance", "Mortgage insurance: USD 120.00 per month."),
])
def test_ordinary_supplied_amount_cannot_be_reclassified_without_context(evidence_cases, field, quote):
    sources = packet_sources(evidence_cases["evidence_complete"])
    issues, errors = accept_issues([ContextIssue(deal="current", field=field, kind="provisional",
        source_id="primary", quote=quote, explanation="The amount has not been externally verified.",
        needs_confirmation=True)], sources)
    assert not issues and errors


def test_correspondence_amount_is_not_an_implied_long_term_guarantee(evidence_cases):
    sources = packet_sources(evidence_cases["evidence_elsewhere"])
    issues, errors = accept_issues([ContextIssue(deal="current", field="home_insurance", kind="provisional",
        source_id="insurance-email", quote="Homeowners insurance for this purchase is USD 165.00 per month.",
        explanation="No long-term stability has been established.", needs_confirmation=True)], sources)
    assert not issues and errors


def test_single_call_has_all_sources_and_exactly_one_model_request(evidence_cases):
    class OneCall:
        calls = 0
        def complete(self, role, schema, payload):
            self.calls += 1
            assert role == "single_call"
            assert "current:insurance-email" in payload["sources"]
            assert "expected" not in str(payload)
            return SingleCallReview(facts=[])
    model = OneCall()
    case = evidence_cases["evidence_elsewhere"]
    report = single_call_review(case, LIMITS, {}, model)
    assert model.calls == report["model_calls"] == 1
    assert report["checks"]["current"]["monthly_total"] == "3235.00"
    assert rules_review(case, LIMITS, {})["checks"]["current"]["monthly_total"] == "3235.00"
