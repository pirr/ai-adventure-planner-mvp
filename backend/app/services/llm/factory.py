from __future__ import annotations

from app.config import Settings, settings as default_settings
from app.services.llm.base import LLMProvider
from app.services.llm.openai_compat import OpenAICompatibleProvider
from app.services.llm.template import TemplateProvider

# Convenience presets — they only set the base_url; model/api_key still come
# from config. "openai" needs an explicit LLM_BASE_URL (or use a preset).
_PRESETS = {
    "llamacpp": "http://localhost:8080/v1",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    # Google Gemini via its OpenAI-compatible endpoint (Bearer key, json_object).
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


def get_llm_provider(settings: Settings = default_settings) -> LLMProvider:
    """Build the configured provider. Falls back to the always-available
    TemplateProvider when disabled, set to 'template', or misconfigured."""
    provider = (settings.llm_provider or "template").strip().lower()
    if not settings.llm_enabled or provider == "template":
        return TemplateProvider()
    base_url = settings.llm_base_url or _PRESETS.get(provider)
    if not base_url:
        return TemplateProvider()
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
        fallback_models=settings.llm_fallback_models,
        gemini_reasoning_effort=settings.gemini_reasoning_effort,
        explain_max_tokens=settings.llm_explain_max_tokens,
    )
