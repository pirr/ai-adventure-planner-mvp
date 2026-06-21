import asyncio

import pytest
from pydantic import ValidationError

from app.schemas import INTEREST_IDS, ParsedSituation, ParseTextRequest
from app.services.llm.template import TemplateProvider


# --- ParsedSituation validation ---------------------------------------------

def test_all_fields_default_to_none_and_is_empty():
    parsed = ParsedSituation()
    assert parsed.is_empty()
    assert not ParsedSituation(available_minutes=60).is_empty()


def test_out_of_range_minutes_rejected():
    with pytest.raises(ValidationError):
        ParsedSituation(available_minutes=10)
    with pytest.raises(ValidationError):
        ParsedSituation(available_minutes=1000)


def test_children_ages_dropped_outside_0_18():
    parsed = ParsedSituation(children_ages=[5, 8, 44, -1])
    assert parsed.children_ages == [5, 8]
    assert ParsedSituation(children_ages=[99]).children_ages is None


def test_interests_whitelisted_and_lowercased():
    parsed = ParsedSituation(interests=["History", "beaches", "surprise me"])
    assert parsed.interests == ["history", "surprise me"]
    assert ParsedSituation(interests=["beaches"]).interests is None


def test_unknown_enum_values_rejected():
    with pytest.raises(ValidationError):
        ParsedSituation(transport_mode="train")


def test_interest_ids_match_the_ui():
    assert INTEREST_IDS == {"history", "fortresses", "viewpoints", "nature", "water", "food", "drinks", "surprise me"}


# --- ParseTextRequest --------------------------------------------------------

def test_parse_text_request_length_limits():
    with pytest.raises(ValidationError):
        ParseTextRequest(text="hi")
    with pytest.raises(ValidationError):
        ParseTextRequest(text="x" * 501)
    assert ParseTextRequest(text="two hours with kids").lang == "en"


# --- provider interface -------------------------------------------------------

def test_template_provider_cannot_parse():
    assert asyncio.run(TemplateProvider().parse_situation("two hours on foot", "en")) is None


# --- OpenAICompatibleProvider.parse_situation ---------------------------------

from app.services.llm.openai_compat import OpenAICompatibleProvider, build_parse_messages  # noqa: E402


def _provider(**kwargs):
    return OpenAICompatibleProvider(base_url="http://llm.test/v1", model="m1", **kwargs)


def _patch_call_model(monkeypatch, content):
    async def fake_call_model(self, *, client, model, messages):
        if isinstance(content, Exception):
            raise content
        return content

    monkeypatch.setattr(OpenAICompatibleProvider, "_call_model", fake_call_model)


def test_parse_situation_returns_validated_fields(monkeypatch):
    _patch_call_model(monkeypatch, '{"available_minutes": 120, "with_dog": true, "interests": ["water"]}')
    parsed = asyncio.run(_provider().parse_situation("2h with my dog near water", "en"))
    assert parsed.available_minutes == 120
    assert parsed.with_dog is True
    assert parsed.interests == ["water"]
    assert parsed.group_type is None


def test_parse_situation_invalid_output_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, '{"available_minutes": 5}')  # below ge=30
    assert asyncio.run(_provider().parse_situation("five minutes", "en")) is None


def test_parse_situation_garbage_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, "sorry, I cannot help with that")
    assert asyncio.run(_provider().parse_situation("blah", "en")) is None


def test_parse_situation_transport_error_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, RuntimeError("LLM unavailable after retries"))
    assert asyncio.run(_provider().parse_situation("2 hours", "en")) is None


def test_parse_messages_mention_whitelist_and_language():
    messages = build_parse_messages("пару часов с собакой", "ru")
    assert messages[0]["role"] == "system"
    assert "surprise me" in messages[0]["content"]      # interest whitelist is in the prompt
    assert messages[-1]["role"] == "user"
    assert "пару часов с собакой" in messages[-1]["content"]


# --- /api/features + /api/parse-request ---------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.llm.base import LLMProvider  # noqa: E402

client = TestClient(app)


class FakeParseProvider(LLMProvider):
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def parse_situation(self, text, lang):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeStorage:
    def __init__(self, grant=1):
        self.grant = grant

    def reserve_api_calls(self, api, anonymous_id, requested, *, daily_limit, user_daily_limit):
        return self.grant


def _enable(monkeypatch, provider, grant=1):
    monkeypatch.setattr("app.main.get_llm_provider", lambda: provider)
    monkeypatch.setattr("app.main.storage", FakeStorage(grant))


def test_features_off_with_template_provider(monkeypatch):
    # Pin the provider: the suite must not depend on the ambient .env LLM config.
    monkeypatch.setattr("app.main.get_llm_provider", lambda: TemplateProvider())
    assert client.get("/api/features").json() == {"parse": False, "require_auth_for_more_recommendations": False}
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 404


def test_features_on_with_real_provider(monkeypatch):
    _enable(monkeypatch, FakeParseProvider())
    assert client.get("/api/features").json() == {"parse": True, "require_auth_for_more_recommendations": False}


def test_parse_request_returns_parsed_fields(monkeypatch):
    provider = FakeParseProvider(result=ParsedSituation(available_minutes=90, with_dog=True))
    _enable(monkeypatch, provider)
    res = client.post("/api/parse-request", json={"text": "1.5h with dog", "anonymous_id": "u"})
    assert res.status_code == 200
    body = res.json()["parsed"]
    assert body["available_minutes"] == 90 and body["with_dog"] is True
    assert provider.calls == 1


def test_parse_request_nothing_recognized(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(result=ParsedSituation()))  # all-None
    res = client.post("/api/parse-request", json={"text": "asdf qwer", "anonymous_id": "u"})
    assert res.status_code == 200 and res.json()["parsed"] is None


def test_parse_request_none_from_provider(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(result=None))
    res = client.post("/api/parse-request", json={"text": "asdf qwer", "anonymous_id": "u"})
    assert res.status_code == 200 and res.json()["parsed"] is None


def test_parse_request_provider_error_is_502(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(error=RuntimeError("boom")))
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 502


def test_parse_request_budget_exhausted_is_429_and_skips_provider(monkeypatch):
    provider = FakeParseProvider(result=ParsedSituation(available_minutes=90))
    _enable(monkeypatch, provider, grant=0)
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 429
    assert provider.calls == 0


def test_parsed_situation_accepts_drinks_interest():
    assert ParsedSituation(interests=["drinks"]).interests == ["drinks"]
    # Unknown interests are still dropped by the whitelist.
    assert ParsedSituation(interests=["nightclub"]).interests is None
