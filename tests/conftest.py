"""Provider doubles exercise the real graph without network or hidden gold labels."""

import pytest

from home_review.fixtures import load_cases
from home_review.models import Decision, Extraction, InvestigationPlan, RiskBrief, SearchOutcome


class FakeModel:
    def __init__(self, cases, *, always_search=False):
        self.cases = cases
        self.calls = []
        self.always_search = always_search

    def complete(self, role, schema, payload):
        self.calls.append((role, payload))
        if role == "extraction":
            for case in self.cases:
                for key in ("current", "saved"):
                    if getattr(case, key).source_text == payload["source_text"]:
                        return Extraction(facts=case.expected_facts[key])
            raise AssertionError("Unknown test packet")
        if role == "investigation":
            unknown = payload["checks"]["current"]["unresolved"]
            return InvestigationPlan(guidance_topic="escrow" if unknown else "none")
        if role == "orchestrator":
            allowed = payload["allowed_actions"]
            if "read_source" in allowed:
                return Decision(action="read_source", source_id=next(iter(payload["unread_sources"])), reason="Inspect evidence before asking.")
            action = "search" if "search" in allowed else "clarify" if "clarify" in allowed else "finalize"
            return Decision(action=action, topic="escrow", reason="Test coordinator follows the unresolved evidence.")
        if role == "risk_review":
            return RiskBrief()
        raise AssertionError(role)


class FakeSearch:
    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or SearchOutcome(error="Tavily is not configured. Local checks can continue.")

    def search(self, topic):
        self.calls.append(topic)
        return self.outcome


@pytest.fixture
def cases():
    return {c.case_id: c for c in load_cases()}


@pytest.fixture
def fake_model(cases):
    return FakeModel(list(cases.values()))

