"""Server-side translations for generated, human-readable text.

Place names and live OSM descriptions are passed through untranslated; only the
text this app composes itself (weather summaries, "why recommended" bullets,
risk warnings, rejected reasons, type descriptions) is localized here.
"""
from __future__ import annotations

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")


def normalize_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


WEATHER_CODE_LABELS: dict[str, dict[int, str]] = {
    "en": {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Heavy drizzle",
        61: "Slight rain",
        63: "Rain",
        65: "Heavy rain",
        80: "Rain showers",
        81: "Rain showers",
        82: "Violent rain showers",
        95: "Thunderstorm",
    },
    "ru": {
        0: "Ясное небо",
        1: "Преимущественно ясно",
        2: "Переменная облачность",
        3: "Пасмурно",
        45: "Туман",
        48: "Изморозь",
        51: "Слабая морось",
        53: "Морось",
        55: "Сильная морось",
        61: "Небольшой дождь",
        63: "Дождь",
        65: "Сильный дождь",
        80: "Кратковременные дожди",
        81: "Кратковременные дожди",
        82: "Сильные ливни",
        95: "Гроза",
    },
}


_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "weather_data_available": "Weather data available",
        "weather_fallback_summary": "Fallback weather: mild and dry conditions assumed.",
        "warn_live_disabled": "Live weather disabled, using fallback weather.",
        "warn_openweather_unavailable": "OpenWeather unavailable, trying fallback provider: {exc}",
        "warn_openmeteo_unavailable": "Open-Meteo unavailable, using fallback weather: {exc}",
        # Place search warnings
        "warn_osm_unavailable": "OpenStreetMap/Overpass unavailable, using fallback places: {exc}",
        "warn_places_disabled": "Live place search disabled, using fallback/sample places.",
        "warn_places_limited": "Live place search returned limited results, supplemented with fallback/sample places.",
        "warn_google_unavailable": "Google Places unavailable, ratings and extra photos skipped: {exc}",
        # Safety / risk warnings
        "warn_rain_paths": "Rain in the last 24 hours may make natural paths muddy or slippery.",
        "warn_rain_heavy": "Heavy recent rainfall increases the risk of poor trail conditions.",
        "warn_high_temp": "High temperature may make walking uncomfortable, especially for children.",
        "warn_high_uv": "High UV index: bring sun protection and water.",
        "warn_strong_wind": "Strong wind may reduce comfort at exposed viewpoints or waterfront areas.",
        "warn_hard_terrain": "This option may involve difficult terrain or climbing.",
        "warn_medium_difficulty": "Some parts may be moderately difficult for an easy walk.",
        "warn_walking_over_limit": "Walking distance is about {km:.1f} km, above your limit of {limit:.1f} km.",
        "warn_after_sunset": "The adventure may finish after sunset.",
        "warn_reduced_mobility": "May be hard with reduced mobility: uneven terrain or longer walking.",
        "warn_elderly": "Pace and terrain may be demanding for older participants.",
        # Why recommended
        "why_fits_time": "Fits your available time window.",
        "why_weather": "Weather looks suitable: {summary}.",
        "why_travel": "Travel time is manageable: about {minutes} min one way.",
        "why_group": "Suitable for the selected group and difficulty level.",
        "why_interest": "Matches your selected interests.",
        "why_preference": "Similar to places you've liked before.",
        "why_walking": "Walking is limited to about {km:.1f} km.",
        "why_best": "This is the best available option after filtering nearby places.",
        # Type descriptions
        "desc_viewpoint": "A scenic stop with a short walk and a strong visual payoff.",
        "desc_fortress": "A history-focused stop with views and exploration potential.",
        "desc_historic_site": "A cultural walk that works well for a short trip.",
        "desc_museum": "A low-risk indoor or semi-indoor option with educational value.",
        "desc_park": "A flexible nature option with easy pacing.",
        "desc_water": "A relaxed water-focused stop suitable for a light outing.",
        "desc_trail": "An active outdoor option that may require more effort.",
        "desc_default": "A nearby place that fits part of your request.",
        # Rejected reasons
        "rej_no_time": "does not fit the available time",
        "rej_safety": "safety/weather risk",
        "rej_group": "not ideal for the selected group",
        "rej_interest": "weak match with interests",
        "rej_lower_score": "lower overall Adventure Score",
    },
    "ru": {
        "weather_data_available": "Данные о погоде доступны",
        "weather_fallback_summary": "Резервная погода: предполагаются мягкие и сухие условия.",
        "warn_live_disabled": "Онлайн-погода отключена, используется резервная погода.",
        "warn_openweather_unavailable": "OpenWeather недоступен, пробуем резервный источник: {exc}",
        "warn_openmeteo_unavailable": "Open-Meteo недоступен, используется резервная погода: {exc}",
        # Place search warnings
        "warn_osm_unavailable": "OpenStreetMap/Overpass недоступен, используются резервные места: {exc}",
        "warn_places_disabled": "Онлайн-поиск мест отключён, используются резервные/демо-места.",
        "warn_places_limited": "Онлайн-поиск мест дал мало результатов, дополнено резервными/демо-местами.",
        "warn_google_unavailable": "Google Places недоступен, рейтинги и дополнительные фото пропущены: {exc}",
        # Safety / risk warnings
        "warn_rain_paths": "Дождь за последние 24 часа мог сделать тропы грязными или скользкими.",
        "warn_rain_heavy": "Сильные недавние осадки повышают риск плохого состояния троп.",
        "warn_high_temp": "Высокая температура может сделать прогулку некомфортной, особенно для детей.",
        "warn_high_uv": "Высокий УФ-индекс: возьмите защиту от солнца и воду.",
        "warn_strong_wind": "Сильный ветер может снизить комфорт на открытых смотровых площадках или у воды.",
        "warn_hard_terrain": "Этот вариант может включать сложный рельеф или подъёмы.",
        "warn_medium_difficulty": "Некоторые участки могут быть умеренно сложными для лёгкой прогулки.",
        "warn_walking_over_limit": "Пешая дистанция около {km:.1f} км, что выше вашего лимита в {limit:.1f} км.",
        "warn_after_sunset": "Приключение может закончиться после заката.",
        "warn_reduced_mobility": "Может быть тяжело при ограниченной мобильности: неровная поверхность или длинная пешая часть.",
        "warn_elderly": "Темп и рельеф могут быть тяжёлыми для пожилых участников.",
        # Why recommended
        "why_fits_time": "Подходит под доступное время.",
        "why_weather": "Погода подходящая: {summary}.",
        "why_travel": "Время в пути приемлемое: около {minutes} мин в одну сторону.",
        "why_group": "Подходит для выбранной группы и уровня сложности.",
        "why_interest": "Соответствует вашим интересам.",
        "why_preference": "Похоже на места, которые вам уже нравились.",
        "why_walking": "Пешая часть ограничена примерно {km:.1f} км.",
        "why_best": "Это лучший доступный вариант после фильтрации мест поблизости.",
        # Type descriptions
        "desc_viewpoint": "Живописная остановка с короткой прогулкой и впечатляющими видами.",
        "desc_fortress": "Историческая остановка с видами и возможностью исследования.",
        "desc_historic_site": "Культурная прогулка, удобная для короткой поездки.",
        "desc_museum": "Безопасный крытый или полукрытый вариант с познавательной ценностью.",
        "desc_park": "Гибкий природный вариант с лёгким темпом.",
        "desc_water": "Спокойная остановка у воды, подходящая для лёгкой прогулки.",
        "desc_trail": "Активный вариант на открытом воздухе, требующий больше усилий.",
        "desc_default": "Место поблизости, которое частично соответствует вашему запросу.",
        # Rejected reasons
        "rej_no_time": "не вписывается в доступное время",
        "rej_safety": "риск по безопасности или погоде",
        "rej_group": "не идеально для выбранной группы",
        "rej_interest": "слабое совпадение с интересами",
        "rej_lower_score": "ниже общий Adventure Score",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Translate ``key`` into ``lang``, formatting with ``kwargs``."""
    lang = normalize_lang(lang)
    template = _MESSAGES[lang].get(key) or _MESSAGES[DEFAULT_LANG][key]
    return template.format(**kwargs) if kwargs else template


def weather_label(lang: str, code: int | None) -> str:
    lang = normalize_lang(lang)
    label = WEATHER_CODE_LABELS[lang].get(code)
    if label is None:
        label = WEATHER_CODE_LABELS[DEFAULT_LANG].get(code)
    return label or t(lang, "weather_data_available")