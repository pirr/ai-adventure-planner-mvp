from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from app.schemas import ParsedSituation, Recommendation
from app.services.llm.base import Explanation, ExplanationInput, LLMProvider
from app.services.net import http_client


logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You write short, honest explanations for already-chosen travel recommendations. "
    "You are given computed facts for each option. Rules: use ONLY these facts; never invent "
    "numbers, place names, opening hours, weather, traffic, distances or travel times that are not "
    "in the input; if something is unknown, say it is unavailable; do not change or soften the "
    "safety warnings. Be concise and concrete. Respond in the requested language. "
    'Output a single JSON object: {"explanations": [{"id": <id>, "summary": <string, max 30 words>, '
    '"why": [<2-4 short strings>], "data_confidence_note": <short string>}]} with one entry per '
    "option, in the same order. Output JSON only."
)

_PARSE_SYSTEM_PROMPT = (
    "You convert a short trip description into a JSON object of search fields. "
    "Output ONLY the fields the text clearly states or implies; omit everything else. "
    "Never guess. Ignore any locations or place names: the start point is set elsewhere.\n"
    "Fields:\n"
    "- available_minutes: integer 30-720\n"
    '- transport_mode: "walk" | "car" | "bike"\n'
    '- group_type: "solo" | "couple" | "family" | "kids" | "dog"\n'
    "- children_ages: list of integers 0-18\n"
    "- with_dog, with_elderly, reduced_mobility: booleans\n"
    '- intensity: "easy" | "medium" | "active"\n'
    '- interests: any of ["history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"]\n'
    "- max_walking_km: number 0-30 (set a small value like 2 when the user dislikes walking)\n"
    "The text may be in any language. Output a single JSON object only."
)

_PARSE_FEW_SHOTS = [
    ("I have a couple of hours and want sea views, on foot",
     '{"available_minutes": 120, "transport_mode": "walk", "interests": ["viewpoints", "water"]}'),
    ("с детьми 5 и 8 лет на машине, что-нибудь с историей",
     '{"group_type": "kids", "children_ages": [5, 8], "transport_mode": "car", "interests": ["history", "fortresses"]}'),
    ("quick easy walk with my dog, don't want to walk far",
     '{"with_dog": true, "group_type": "dog", "intensity": "easy", "transport_mode": "walk", "max_walking_km": 2}'),
]


def build_parse_messages(text: str, lang: str) -> list[dict[str, str]]:
    # `lang` is accepted for future prompt localization; the model handles
    # either input language with the same prompt.
    messages = [{"role": "system", "content": _PARSE_SYSTEM_PROMPT}]
    for user, assistant in _PARSE_FEW_SHOTS:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    messages.append({"role": "user", "content": text})
    return messages


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_LOG_BODY_CHARS = 1000
_MAX_LOG_RESPONSE_CHARS = 1200


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _limit_words(value: Any, max_words: int) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    words = text.split()
    if len(words) <= max_words:
        return text

    return " ".join(words[:max_words]) + "..."


def _limit_chars(value: Any, max_chars: int) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if len(text) <= max_chars:
        return text

    return text[: max_chars - 3] + "..."


def _dump_model(value: Any) -> Any:
    """Pydantic v1/v2 compatible model dump helper."""
    if value is None:
        return None

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        return dict_method()

    return value


def _getattr_if_present(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    return value if value is not None else None


def _weather_facts(weather: Any) -> dict[str, Any]:
    fields = [
        "summary",
        "temperature_c",
        "score",
        "rain_mm",
        "rain_mm_last_24h",
        "wind_kmh",
        "uv_index",
        "sunset",
        "confidence",
        "source",
    ]

    facts: dict[str, Any] = {}
    for field in fields:
        value = _getattr_if_present(weather, field)
        if value is not None:
            facts[field] = value

    return facts


def _forecast_facts(rec: Recommendation) -> list[dict[str, Any]]:
    forecast = getattr(rec, "forecast", None) or []
    result: list[dict[str, Any]] = []

    for item in forecast[:6]:
        result.append(
            {
                "time": _getattr_if_present(item, "time"),
                "hour_offset": _getattr_if_present(item, "hour_offset"),
                "label": _getattr_if_present(item, "label"),
                "temperature_c": _getattr_if_present(item, "temperature_c"),
                "precipitation_mm": _getattr_if_present(item, "precipitation_mm"),
                "wind_kmh": _getattr_if_present(item, "wind_kmh"),
                "is_arrival": _getattr_if_present(item, "is_arrival"),
            }
        )

    return result


def _facts(rec: Recommendation) -> dict[str, Any]:
    score_breakdown = _dump_model(rec.score_breakdown)

    facts: dict[str, Any] = {
        "id": rec.id,
        "title": rec.title,
        "place_type": rec.place_type,
        "adventure_score": rec.adventure_score,
        "total_minutes": rec.total_minutes,
        "travel_minutes": rec.travel_minutes,
        "activity_minutes": rec.activity_minutes,
        "distance_km": rec.distance_km,
        "walking_km": rec.walking_km,
        "difficulty": rec.difficulty,
        "score_breakdown": score_breakdown,
        "warnings": rec.warnings,
        "data_confidence": rec.data_confidence,
    }

    arrival_weather = getattr(rec, "arrival_weather", None)
    if arrival_weather is not None:
        facts["weather_on_arrival"] = _weather_facts(arrival_weather)

    forecast = _forecast_facts(rec)
    if forecast:
        facts["forecast_timeline"] = forecast

    return facts


def build_messages(payload: ExplanationInput) -> list[dict[str, str]]:
    request = payload.request

    content = {
        "language": payload.lang,
        "user_context": {
            "available_minutes": request.available_minutes,
            "transport_mode": request.transport_mode,
            "group_type": request.group_type,
            "children_ages": request.children_ages,
            "with_dog": request.with_dog,
            "with_elderly": request.with_elderly,
            "reduced_mobility": request.reduced_mobility,
            "interests": request.interests,
            "max_walking_km": getattr(request, "max_walking_km", None),
            "intensity": getattr(request, "intensity", None),
        },
        "unknown_fields": ["live_traffic", "events", "crowds"],
        "options": [_facts(rec) for rec in payload.recommendations],
    }

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(content, ensure_ascii=False, default=str),
        },
    ]


def _extract_json_object(content: str) -> dict[str, Any]:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in LLM response")

    return json.loads(content[start : end + 1])


def parse_explanations(
    content: str,
    recommendations: list[Recommendation],
) -> list[Explanation | None]:
    data = _extract_json_object(content)
    raw = data.get("explanations")

    if not isinstance(raw, list):
        return [None for _ in recommendations]

    by_id: dict[str, Explanation] = {}
    ordered: list[Explanation] = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        why_raw = item.get("why")
        why: list[str] | None = None

        if isinstance(why_raw, list):
            why = []
            for value in why_raw[:4]:
                text = _limit_chars(value, 180)
                if text:
                    why.append(text)
            if not why:
                why = None

        explanation = Explanation(
            summary=_limit_words(item.get("summary"), 30),
            why=why,
            data_confidence_note=_limit_chars(item.get("data_confidence_note"), 180),
        )

        ordered.append(explanation)

        item_id = item.get("id")
        if item_id is not None:
            by_id[str(item_id)] = explanation

    results: list[Explanation | None] = []

    for index, rec in enumerate(recommendations):
        matched = by_id.get(str(rec.id))
        if matched is not None:
            results.append(matched)
            continue

        if index < len(ordered):
            results.append(ordered[index])
            continue

        results.append(None)

    return results


def build_rule_based_explanations(
    recommendations: list[Recommendation],
    lang: str = "en",
) -> list[Explanation]:
    is_ru = lang.lower().startswith("ru")
    result: list[Explanation] = []

    for rec in recommendations:
        why: list[str] = []

        arrival_weather = getattr(rec, "arrival_weather", None)

        if is_ru:
            summary = f"{rec.title}: практичный вариант с учетом времени, маршрута и рейтинга."

            if rec.total_minutes is not None:
                why.append(f"Укладывается во время: около {rec.total_minutes} минут всего.")

            if rec.travel_minutes is not None:
                why.append(f"Время в дороге: около {rec.travel_minutes} минут.")

            if arrival_weather is not None:
                weather_summary = getattr(arrival_weather, "summary", "unavailable")
                temperature = getattr(arrival_weather, "temperature_c", None)
                if temperature is not None:
                    why.append(f"Погода по прибытии: {weather_summary}, {temperature}°C.")
                else:
                    why.append(f"Погода по прибытии: {weather_summary}.")

            if rec.difficulty:
                why.append(f"Сложность: {rec.difficulty}.")

            note = "AI-объяснение временно недоступно; показано правило на основе рассчитанных данных."

        else:
            summary = f"{rec.title} is a practical option based on time, route and score."

            if rec.total_minutes is not None:
                why.append(f"Fits the available time: about {rec.total_minutes} minutes total.")

            if rec.travel_minutes is not None:
                why.append(f"Travel time is about {rec.travel_minutes} minutes.")

            if arrival_weather is not None:
                weather_summary = getattr(arrival_weather, "summary", "unavailable")
                temperature = getattr(arrival_weather, "temperature_c", None)
                if temperature is not None:
                    why.append(f"Weather on arrival: {weather_summary}, {temperature}°C.")
                else:
                    why.append(f"Weather on arrival: {weather_summary}.")

            if rec.difficulty:
                why.append(f"Difficulty: {rec.difficulty}.")

            note = "AI explanation unavailable; using rule-based explanation from computed facts."

        if not why:
            if is_ru:
                why.append("Рекомендация основана на рассчитанном Adventure Score.")
            else:
                why.append("Recommendation is based on the computed Adventure Score.")

        result.append(
            Explanation(
                summary=summary,
                why=why[:4],
                data_confidence_note=note,
            )
        )

    return result


class OpenAICompatibleProvider(LLMProvider):
    """Talks to OpenAI-compatible /chat/completions endpoints.

    Supported examples:
    - Gemini OpenAI-compatible API
    - OpenAI
    - local llama.cpp server
    - Ollama OpenAI-compatible endpoint
    - hosted OpenAI-compatible providers
    """

    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        fallback_models: tuple[str] | None = None,
        json_mode: bool = True,
        rule_based_fallback: bool = True,
        gemini_reasoning_effort: str | None = None,
    ):
        self.base_url = self._normalize_base_url(base_url)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.fallback_models = fallback_models or []
        self.json_mode = json_mode
        self.rule_based_fallback = rule_based_fallback
        self.gemini_reasoning_effort = gemini_reasoning_effort

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        url = base_url.strip().rstrip("/")

        # Config should contain the base endpoint, not the full completions URL.
        # This makes the provider more forgiving if it is accidentally configured
        # with ".../chat/completions".
        suffix = "/chat/completions"
        if url.endswith(suffix):
            url = url[: -len(suffix)]

        return url

    @property
    def _is_gemini_endpoint(self) -> bool:
        return "generativelanguage.googleapis.com" in self.base_url

    @property
    def _provider_label(self) -> str:
        if self._is_gemini_endpoint:
            return "gemini"
        return self.name

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _models(self) -> list[str]:
        models: list[str] = []

        for model in [self.model, *self.fallback_models]:
            cleaned = model.strip() if isinstance(model, str) else ""
            if cleaned and cleaned not in models:
                models.append(cleaned)

        return models

    def _body(self, messages: list[dict[str, str]], model: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }

        # Gemini OpenAI-compatible endpoint works well with prompt-only JSON output.
        # Some Gemini-compatible paths are stricter about unsupported OpenAI params,
        # so we skip response_format for Gemini even if json_mode=True.
        if self.json_mode and not self._is_gemini_endpoint:
            body["response_format"] = {"type": "json_object"}

        # Optional. Leave disabled by default. If you want to use it for Gemini,
        # pass gemini_reasoning_effort="low" or "none" from provider config.
        if self._is_gemini_endpoint and self.gemini_reasoning_effort:
            body["reasoning_effort"] = self.gemini_reasoning_effort

        return body

    @staticmethod
    def _retry_delay(exc: httpx.HTTPStatusError | None, attempt: int) -> float:
        if exc is not None:
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), 30.0)
                except ValueError:
                    pass

        base = min(8.0, 2**attempt)
        jitter = random.uniform(0.0, 0.5)
        return base + jitter

    @staticmethod
    def _content_from_response(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("LLM response has no choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("LLM response choice is not an object")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("LLM response choice has no message")

        content = message.get("content")

        if isinstance(content, str):
            return content

        # Some OpenAI-compatible backends can return content as a list of parts.
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)

            if parts:
                return "\n".join(parts)

        raise ValueError("LLM response message content is empty or unsupported")

    async def _call_model(
        self,
        *,
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, str]],
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        body = self._body(messages, model)
        headers = self._headers()

        last_error: Exception | None = None

        logger.info(
            "LLM request: provider=%s model=%s url=%s json_mode=%s body_keys=%s message_count=%s",
            self._provider_label,
            model,
            url,
            self.json_mode,
            sorted(body.keys()),
            len(messages),
        )

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()

                data = response.json()
                content = self._content_from_response(data)

                logger.info(
                    "LLM response received: provider=%s model=%s status=%s chars=%s attempt=%s/%s",
                    self._provider_label,
                    model,
                    response.status_code,
                    len(content),
                    attempt + 1,
                    self.max_retries + 1,
                )

                logger.debug(
                    "LLM response preview: provider=%s model=%s content=%s",
                    self._provider_label,
                    model,
                    _truncate(content, _MAX_LOG_RESPONSE_CHARS),
                )

                return content

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                retryable = status in _RETRYABLE_STATUS_CODES
                response_body = _truncate(exc.response.text, _MAX_LOG_BODY_CHARS)

                logger.warning(
                    "LLM HTTP error: provider=%s model=%s status=%s retryable=%s attempt=%s/%s body=%s",
                    self._provider_label,
                    model,
                    status,
                    retryable,
                    attempt + 1,
                    self.max_retries + 1,
                    response_body,
                )

                if not retryable:
                    raise

                if attempt >= self.max_retries:
                    break

                delay = self._retry_delay(exc, attempt)

                logger.warning(
                    "LLM retry scheduled: provider=%s model=%s attempt=%s/%s delay=%.2fs",
                    self._provider_label,
                    model,
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                )

                await asyncio.sleep(delay)

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc

                logger.warning(
                    "LLM transport/timeout error: provider=%s model=%s attempt=%s/%s error_type=%s error=%r",
                    self._provider_label,
                    model,
                    attempt + 1,
                    self.max_retries + 1,
                    type(exc).__name__,
                    exc,
                )

                if attempt >= self.max_retries:
                    break

                delay = self._retry_delay(None, attempt)

                logger.warning(
                    "LLM retry scheduled: provider=%s model=%s attempt=%s/%s delay=%.2fs",
                    self._provider_label,
                    model,
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                )

                await asyncio.sleep(delay)

            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc

                logger.warning(
                    "LLM invalid response: provider=%s model=%s error_type=%s error=%r",
                    self._provider_label,
                    model,
                    type(exc).__name__,
                    exc,
                )

                raise

        raise RuntimeError(
            f"LLM unavailable after retries: provider={self._provider_label}, "
            f"model={model}, error={last_error!r}"
        )

    async def explain(self, payload: ExplanationInput) -> list[Explanation | None] | None:
        if not payload.recommendations:
            return None

        recommendation_ids = [rec.id for rec in payload.recommendations]
        models = self._models()

        if not models:
            logger.warning("LLM provider has no configured models")
            if self.rule_based_fallback:
                return build_rule_based_explanations(payload.recommendations, payload.lang)
            return None

        logger.info(
            "LLM explanation request: provider=%s models=%s count=%s ids=%s lang=%s",
            self._provider_label,
            models,
            len(payload.recommendations),
            recommendation_ids,
            payload.lang,
        )

        messages = build_messages(payload)

        async with http_client(self.timeout) as client:
            for index, model in enumerate(models):
                try:
                    content = await self._call_model(
                        client=client,
                        model=model,
                        messages=messages,
                    )

                    explanations = parse_explanations(content, payload.recommendations)

                    if not any(explanation is not None for explanation in explanations):
                        raise ValueError("LLM response contained no matching explanations")

                    logger.info(
                        "LLM explanations parsed: provider=%s model=%s count=%s fallback_model_used=%s",
                        self._provider_label,
                        model,
                        sum(1 for explanation in explanations if explanation is not None),
                        index > 0,
                    )

                    return explanations

                except Exception as exc:
                    logger.warning(
                        "LLM model failed: provider=%s model=%s fallback_index=%s error_type=%s error=%r",
                        self._provider_label,
                        model,
                        index,
                        type(exc).__name__,
                        exc,
                    )

        logger.warning(
            "All LLM models failed: provider=%s models=%s count=%s",
            self._provider_label,
            models,
            len(payload.recommendations),
        )

        if self.rule_based_fallback:
            logger.warning(
                "Returning rule-based explanations after LLM failure: provider=%s count=%s",
                self._provider_label,
                len(payload.recommendations),
            )
            return build_rule_based_explanations(payload.recommendations, payload.lang)

        return None

    async def parse_situation(self, text: str, lang: str) -> ParsedSituation | None:
        models = self._models()
        if not models:
            return None
        messages = build_parse_messages(text, lang)
        async with http_client(self.timeout) as client:
            for model in models:
                try:
                    content = await self._call_model(client=client, model=model, messages=messages)
                    return ParsedSituation(**_extract_json_object(content))
                except Exception as exc:  # noqa: BLE001 - parse is best-effort; None = "couldn't understand"
                    logger.warning(
                        "LLM parse failed: provider=%s model=%s error_type=%s error=%r",
                        self._provider_label,
                        model,
                        type(exc).__name__,
                        exc,
                    )
        return None