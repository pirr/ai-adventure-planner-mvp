import asyncio
import contextlib
import json

import httpx

from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services.llm import openai_compat
from app.services.llm.base import ExplanationInput
from app.services.llm.openai_compat import OpenAICompatibleProvider, build_messages, parse_explanations


def _rec(rec_id="r1", **over) -> Recommendation:
    base = dict(
        id=rec_id,
        title="Old Fort",
        place_type="fortress",
        lat=42.4,
        lon=18.7,
        adventure_score=86,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120,
        travel_minutes=36,
        activity_minutes=80,
        distance_km=10.0,
        walking_km=2.0,
        difficulty="easy",
        description="A history-focused stop.",
        why=["Fits your time."],
        warnings=[],
        map_url="https://maps.example/x",
        source="test",
    )
    base.update(over)
    return Recommendation(**base)


def _payload(recs):
    return ExplanationInput(request=AdventureRequest(lat=42.4, lon=18.7), recommendations=recs, lang="en")


def _content(explanations):
    return {"choices": [{"message": {"content": json.dumps({"explanations": explanations})}}]}


# --- pure functions ---------------------------------------------------------

def test_parse_explanations_matches_by_id_regardless_of_order():
    recs = [_rec("a"), _rec("b")]
    content = json.dumps({"explanations": [
        {"id": "b", "summary": "B summary", "why": ["x"]},
        {"id": "a", "summary": "A summary", "why": ["y"]},
    ]})
    out = parse_explanations(content, recs)
    assert out[0].summary == "A summary"
    assert out[1].summary == "B summary"


def test_parse_explanations_falls_back_to_order_without_ids():
    recs = [_rec("a"), _rec("b")]
    content = json.dumps({"explanations": [{"summary": "first"}, {"summary": "second"}]})
    out = parse_explanations(content, recs)
    assert out[0].summary == "first"
    assert out[1].summary == "second"


def test_parse_explanations_enforces_limits():
    long_summary = " ".join(["word"] * 60)
    long_why = "y" * 400
    content = json.dumps({"explanations": [{"id": "r1", "summary": long_summary, "why": [long_why]}]})
    out = parse_explanations(content, [_rec("r1")])
    assert len(out[0].summary.split()) <= 31  # 30 words + an ellipsis token
    assert len(out[0].why[0]) <= 180


def test_parse_explanations_handles_fenced_json_and_prose():
    content = "Sure!\n```json\n" + json.dumps({"explanations": [{"id": "r1", "summary": "ok"}]}) + "\n```"
    out = parse_explanations(content, [_rec("r1")])
    assert out[0].summary == "ok"


def test_parse_explanations_non_list_returns_none():
    out = parse_explanations(json.dumps({"explanations": "nope"}), [_rec("r1")])
    assert out == [None]


def test_models_filters_empty_and_duplicates_preserving_order():
    provider = OpenAICompatibleProvider(base_url="https://x/v1", model="m1", fallback_models=("", "m2", "m1", "  "))
    assert provider._models() == ["m1", "m2"]


def test_content_from_response_string_and_list_parts():
    assert OpenAICompatibleProvider._content_from_response(_content([{"summary": "s"}])).startswith("{")
    listed = {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
    assert OpenAICompatibleProvider._content_from_response(listed) == "a\nb"


def test_content_from_response_errors_without_choices():
    try:
        OpenAICompatibleProvider._content_from_response({"choices": []})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_body_skips_response_format_for_gemini():
    gemini = OpenAICompatibleProvider(base_url="https://generativelanguage.googleapis.com/v1beta/openai", model="gemini-2.5-flash", gemini_reasoning_effort="low")
    body = gemini._body(build_messages(_payload([_rec()])), "gemini-2.5-flash")
    assert "response_format" not in body
    assert body["reasoning_effort"] == "low"


def test_body_includes_response_format_for_non_gemini():
    openai = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="gpt-4o-mini")
    body = openai._body(build_messages(_payload([_rec()])), "gpt-4o-mini")
    assert body["response_format"] == {"type": "json_object"}
    no_json = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="m", json_mode=False)
    assert "response_format" not in no_json._body(build_messages(_payload([_rec()])), "m")


def test_retry_delay_honors_retry_after_header():
    request = httpx.Request("POST", "http://x")
    response = httpx.Response(429, headers={"Retry-After": "5"}, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert OpenAICompatibleProvider._retry_delay(exc, 0) == 5.0
    assert 2.0 <= OpenAICompatibleProvider._retry_delay(None, 1) <= 2.5


def test_normalize_base_url_strips_slash_and_completions_suffix():
    assert OpenAICompatibleProvider._normalize_base_url("https://x/v1/") == "https://x/v1"
    assert OpenAICompatibleProvider._normalize_base_url("https://x/v1/chat/completions") == "https://x/v1"


# --- network paths via MockTransport ---------------------------------------

def _patch(monkeypatch, handler):
    @contextlib.asynccontextmanager
    async def fake_client(timeout=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(openai_compat, "http_client", fake_client)

    async def _no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(openai_compat.asyncio, "sleep", _no_sleep)


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_content([{"id": "r1", "summary": "hello", "why": ["a"]}]))

    _patch(monkeypatch, handler)
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="m1", max_retries=2)
    out = asyncio.run(provider.explain(_payload([_rec("r1")])))
    assert calls["n"] == 2
    assert out[0].summary == "hello"


def test_non_retryable_400_fails_over_to_next_model(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "bad":
            return httpx.Response(400, json={"error": "bad model"})
        return httpx.Response(200, json=_content([{"id": "r1", "summary": "from good", "why": ["a"]}]))

    _patch(monkeypatch, handler)
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="bad", fallback_models=("good",))
    out = asyncio.run(provider.explain(_payload([_rec("r1")])))
    assert seen == ["bad", "good"]
    assert out[0].summary == "from good"


def test_all_models_fail_uses_rule_based_or_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "nope"})

    _patch(monkeypatch, handler)
    with_fallback = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="m", rule_based_fallback=True)
    out = asyncio.run(with_fallback.explain(_payload([_rec("r1")])))
    assert out[0] is not None and out[0].summary  # rule-based explanation

    without = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="m", rule_based_fallback=False)
    assert asyncio.run(without.explain(_payload([_rec("r1")]))) is None


def test_parse_prompt_includes_drinks_and_example():
    from app.services.llm.openai_compat import _PARSE_SYSTEM_PROMPT, build_parse_messages

    assert "drinks" in _PARSE_SYSTEM_PROMPT
    messages = build_parse_messages("I want to drink a beer nearby", "en")
    assert any("drinks" in m["content"] for m in messages if m["role"] == "assistant")


def test_explain_body_includes_max_tokens():
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com/v1", model="gpt-4o-mini", explain_max_tokens=500,
    )
    body = provider._body([{"role": "user", "content": "x"}], "gpt-4o-mini", max_tokens=500)
    assert body["max_tokens"] == 500


def test_body_without_max_tokens_omits_it():
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="gpt-4o-mini")
    body = provider._body([{"role": "user", "content": "x"}], "gpt-4o-mini")
    assert "max_tokens" not in body
