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
    assert INTEREST_IDS == {"history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"}


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
