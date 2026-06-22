from __future__ import annotations

import hashlib

from app.config import settings
from app.schemas import AdventureRequest
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.llm.template import TemplateProvider


def ab_bucket(anonymous_id: str) -> int:
    """Stable 0/1 bucket from the anonymous id (hashlib, not the salted hash())."""
    return int(hashlib.sha1(anonymous_id.encode()).hexdigest(), 16) % 2


def explainer_provider(request: AdventureRequest) -> LLMProvider:
    """The configured LLM provider, unless the A/B control bucket is selected
    (then templates). No-op when A/B is off or no LLM/anonymous_id is present."""
    provider = get_llm_provider()
    if not settings.ab_test_enabled or isinstance(provider, TemplateProvider) or not request.anonymous_id:
        return provider
    return provider if ab_bucket(request.anonymous_id) == 1 else TemplateProvider()
