import json

import httpx
import pytest

from home_review.models import Decision
from home_review.providers import NebiusGateway, ProviderError, Settings, TavilyGateway, approved_url


@pytest.mark.parametrize("url, expected", [
    ("https://www.consumerfinance.gov/owning-a-home/", True),
    ("https://hud.gov/", True),
    ("https://www.hud.gov/", True),
    ("https://consumerfinance.gov.attacker.example/", False),
    ("https://attacker.example/consumerfinance.gov", False),
    ("http://www.hud.gov/", False),
    ("https://secret@hud.gov/", False),
    ("https://hud.gov:8080/", False),
    ("javascript:alert(1)", False),
    ("https://hud.gov:bad/", False),
])
def test_source_allowlist(url, expected):
    assert approved_url(url) is expected


def config(key=""):
    return Settings("dummy-nebius-key", "example-model", "https://api.tokenfactory.nebius.com/v1/", key)


def test_missing_tavily_key_does_not_make_a_request(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("No HTTP request expected"))
    result = TavilyGateway(config()).search("escrow")
    assert not result.sources
    assert "not configured" in result.error


def install_transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))


def test_search_uses_generic_query_and_verified_page_content(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["include_domains"] == ["consumerfinance.gov", "hud.gov"]
        assert payload["include_answer"] is False
        assert payload["max_results"] == 3
        assert "borrower" not in payload["query"].lower()
        return httpx.Response(200, json={"answer": "DO NOT USE THIS GENERATED ANSWER", "results": [
            {"url": "https://www.consumerfinance.gov/owning-a-home/loan-estimate/", "title": "Loan Estimate", "raw_content": "Taxes and insurance that are not escrowed may need to be paid directly. Ask which expenses are included in the monthly payment."},
            {"url": "https://attacker.example", "title": "Fake", "raw_content": "Escrow " * 50},
        ]})
    install_transport(monkeypatch, handler)
    result = TavilyGateway(config("dummy-tavily-key")).search("escrow")
    assert result.error is None and len(result.sources) == 1
    assert "not escrowed" in result.sources[0].passage
    assert "GENERATED ANSWER" not in str(result)


def test_snippets_are_not_substituted_for_page_content(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"results": [
        {"url": "https://hud.gov/test", "content": "A search snippet about escrow", "raw_content": None},
    ]}))
    result = TavilyGateway(config("dummy")).search("escrow")
    assert not result.sources and result.error


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_search_http_errors_are_sanitized(monkeypatch, status):
    install_transport(monkeypatch, lambda request: httpx.Response(status, text="dummy-secret-must-not-appear"))
    result = TavilyGateway(config("dummy-secret-must-not-appear")).search("escrow")
    assert f"HTTP {status}" in result.error
    assert "dummy-secret" not in result.error


def test_malformed_search_json_is_handled(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, text="not JSON"))
    result = TavilyGateway(config("dummy")).search("escrow")
    assert not result.sources and "could not be completed" in result.error


def test_model_errors_do_not_expose_provider_response():
    class BadClient:
        def with_structured_output(self, *args, **kwargs):
            raise RuntimeError("dummy-private-token")
    gateway = object.__new__(NebiusGateway)
    gateway.client = BadClient()
    with pytest.raises(ProviderError) as error:
        gateway.complete("orchestrator", Decision, {})
    assert "dummy-private-token" not in str(error.value)


def test_credentials_are_not_in_settings_repr():
    assert "dummy-nebius-key" not in repr(config("dummy-tavily-key"))

