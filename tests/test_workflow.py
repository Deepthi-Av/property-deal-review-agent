import pytest

from home_review.models import CitedStatement, Decision, Guidance, Limits, RiskBrief, SearchOutcome
from home_review.providers import ProviderError
from home_review.workflow import ReviewService
from conftest import FakeSearch

LIMITS = Limits(monthly="3300", cash="95000")


def test_complete_review_and_gold_labels_not_in_model_context(cases, fake_model):
    service = ReviewService(fake_model, FakeSearch())
    review_id = service.start(cases["clean"], LIMITS)
    snap = service.snapshot(review_id)
    assert snap["pending"] is None
    assert snap["state"]["report"]["checks"]["current"]["monthly_total"] == "3220.00"
    assert snap["state"]["report"]["search_count"] == 0
    assert "expected_facts" not in str(fake_model.calls)
    assert {role for role, _ in fake_model.calls} == {"extraction", "investigation", "orchestrator", "risk_review"}


def test_missing_key_handoff_resume_and_no_reextraction(cases, fake_model):
    search = FakeSearch()
    service = ReviewService(fake_model, search)
    review_id = service.start(cases["missing"], LIMITS)
    assert service.snapshot(review_id)["pending"]["fields"] == ["home_insurance"]
    service.resume(review_id, {"home_insurance": "150"})
    report = service.snapshot(review_id)["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3220.00"
    assert report["clarification_used"]
    assert len(search.calls) == 1
    assert sum(role == "extraction" for role, _ in fake_model.calls) == 2
    assert "reviewer" in str(report["checks"]["current"]["citations"])


def test_invalid_resume_leaves_interrupt_pending(cases, fake_model):
    service = ReviewService(fake_model, FakeSearch())
    review_id = service.start(cases["missing"], LIMITS)
    with pytest.raises(ValueError):
        service.resume(review_id, {"home_insurance": "-1"})
    assert service.snapshot(review_id)["pending"]


def test_one_handoff_and_two_searches_maximum(cases, fake_model):
    search = FakeSearch(SearchOutcome(error="No readable source was returned."))
    service = ReviewService(fake_model, search)
    review_id = service.start(cases["conflict"], LIMITS)
    service.resume(review_id, {"hoa": None})
    snap = service.snapshot(review_id)
    assert snap["pending"] is None
    assert len(search.calls) == 2
    assert snap["state"]["report"]["checks"]["current"]["monthly_status"] == "unknown"
    assert snap["state"]["decisions"] <= 5


def test_unexpected_search_failure_is_visible(cases, fake_model):
    class BrokenSearch:
        def search(self, topic):
            raise RuntimeError("secret must not escape")
    service = ReviewService(fake_model, BrokenSearch())
    review_id = service.start(cases["missing"], LIMITS)
    errors = service.snapshot(review_id)["state"]["errors"]
    assert any("failed unexpectedly" in error for error in errors)
    assert "secret" not in str(errors)


def test_fatal_extraction_failure_returns_no_invented_review(cases):
    class BrokenModel:
        def complete(self, role, schema, payload):
            raise ProviderError("Provider unavailable")
    service = ReviewService(BrokenModel(), FakeSearch())
    snap = service.snapshot(service.start(cases["clean"], LIMITS))
    assert snap["state"]["report"]["fatal"]
    assert "checks" not in snap["state"]["report"]


def test_invalid_coordinator_action_is_blocked(cases, fake_model):
    original = fake_model.complete
    def invalid_action(role, schema, payload):
        if role == "orchestrator":
            return Decision(action="search", topic="none", reason="Invalid request")
        return original(role, schema, payload)
    fake_model.complete = invalid_action
    service = ReviewService(fake_model, FakeSearch())
    snap = service.snapshot(service.start(cases["clean"], LIMITS))
    assert snap["state"]["report"]["search_count"] == 0
    assert any("unsupported" in message for message in snap["state"]["errors"])


def test_risk_review_cannot_introduce_uncited_findings(cases, fake_model):
    original = fake_model.complete
    def invented(role, schema, payload):
        if role == "risk_review":
            return RiskBrief(observations=[CitedStatement(text="Guaranteed profit", source_refs=["invented-source"])])
        return original(role, schema, payload)
    fake_model.complete = invented
    service = ReviewService(fake_model, FakeSearch())
    report = service.snapshot(service.start(cases["clean"], LIMITS))["state"]["report"]
    assert "Guaranteed profit" not in str(report)


def test_review_ids_isolate_state(cases, fake_model):
    service = ReviewService(fake_model, FakeSearch())
    first = service.start(cases["clean"], LIMITS)
    second = service.start(cases["over_limit"], LIMITS)
    assert first != second
    assert service.snapshot(first)["state"]["report"]["checks"]["current"]["monthly_status"] == "pass"
    assert service.snapshot(second)["state"]["report"]["checks"]["current"]["monthly_status"] == "fail"


def test_omitting_a_conflict_cannot_bypass_human_review(cases, fake_model):
    original = fake_model.complete
    def omit_conflict(role, schema, payload):
        result = original(role, schema, payload)
        if role == "extraction" and payload["source_text"] == cases["conflict"].current.source_text:
            result.facts = result.facts[:-1]
        return result
    fake_model.complete = omit_conflict
    service = ReviewService(fake_model, FakeSearch())
    snap = service.snapshot(service.start(cases["conflict"], LIMITS))
    assert snap["pending"]["fields"] == ["hoa"]
    assert snap["state"]["checks"]["current"]["monthly_total"] is None
    assert any("audit recovered" in message for message in snap["state"]["errors"])


def test_readable_guidance_is_preserved_and_not_searched_again(cases, fake_model):
    search = FakeSearch(SearchOutcome(sources=[Guidance(
        citation_id="W1", title="Test official guidance", url="https://www.consumerfinance.gov/owning-a-home/loan-estimate/",
        passage="This is a mocked official-source passage for the workflow test, not a live retrieval.",
        retrieved_at="2026-10-23T00:00:00+00:00",
    )]))
    service = ReviewService(fake_model, search)
    review_id = service.start(cases["missing"], LIMITS)
    service.resume(review_id, {"home_insurance": "150"})
    report = service.snapshot(review_id)["state"]["report"]
    assert len(search.calls) == 1
    assert report["guidance"][0]["citation_id"] == "W1"
    assert report["checks"]["current"]["monthly_total"] == "3220.00"

