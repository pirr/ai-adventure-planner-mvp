const $ = (id) => document.getElementById(id);
const resultsEl = $('results');
const cardsEl = $('cards');
const loadingEl = $('loading');
const errorBox = $('errorBox');
let lastRequestId = null;
let lastResponse = null;

// Anonymous, persistent per-browser id (no accounts/PII). Ties a user's
// sessions, feedback and events together for history and personalization.
function anonymousId() {
  let id = localStorage.getItem('anon_id');
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `a-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem('anon_id', id);
  }
  return id;
}

// ---------------------------------------------------------------------------
// Internationalization (EN / RU)
// ---------------------------------------------------------------------------
const I18N = {
  en: {
    app_title: 'AI Adventure Planner MVP',
    hero_title: 'Find a mini-adventure nearby',
    hero_subtitle:
      'Enter your time, transport and interests. The app ranks nearby places using weather, route effort and fit.',
    use_location: 'Use my location',
    use_demo: 'Use Tivat demo',
    loc_not_set: 'Location is not set yet.',
    trip_request: 'Trip request',
    free_text: 'Free text',
    request_text_default: 'Family trip for 5 hours with fortress, history and views.',
    latitude: 'Latitude',
    longitude: 'Longitude',
    available_time: 'Available time',
    opt_30min: '30 min',
    opt_1h: '1 hour',
    opt_2h: '2 hours',
    opt_4h: '4 hours',
    opt_5h: '5 hours',
    opt_allday: 'All day',
    transport: 'Transport',
    t_walk: 'Walk',
    t_car: 'Car',
    t_bike: 'Bike',
    group: 'Group',
    g_solo: 'Solo',
    g_couple: 'Couple',
    g_family: 'Family',
    g_dog: 'Dog',
    intensity: 'Intensity',
    i_easy: 'Easy',
    i_medium: 'Medium',
    i_active: 'Active',
    children_ages: 'Children ages',
    max_walking: 'Max walking km',
    context: 'Context',
    with_dog: 'With a dog',
    with_elderly: 'With older adults',
    reduced_mobility: 'Reduced mobility',
    interests: 'Interests',
    c_history: 'History',
    c_fortresses: 'Fortresses',
    c_viewpoints: 'Viewpoints',
    c_nature: 'Nature',
    c_water: 'Water',
    c_food: 'Food',
    c_surprise: 'Surprise me',
    use_live: 'Use live OSM / weather / routing when available',
    find_adventure: 'Find adventure',
    loading_title: 'Analyzing options',
    loading_subtitle: 'Checking places, weather, travel time and risk rules.',
    results_title: 'Recommendations',
    history_title: 'Recently seen',
    clear_history: 'Clear my history',
    history_opened: 'opened',
    history_cleared: 'History cleared.',
    history_confirm: 'Delete your local history (recent searches, feedback and events)?',
    // recommendation card chrome
    score_breakdown: 'Score breakdown',
    open_maps: 'Google Maps',
    open_apple_maps: 'Apple Maps',
    useful: '👍 Useful',
    not_useful: '👎 Not useful',
    photo_source: 'Photo: {source}',
    // per-place destination weather
    place_weather_title: 'Weather at destination',
    on_arrival: 'On arrival',
    forecast_arrival: 'arrival',
    // weather box (user's current location)
    weather_context: 'Current location weather',
    src_live: 'Live',
    src_fallback: 'Fallback',
    weather_fit: 'Weather Fit {score}/100',
    rain_badge: 'Rain 24h: {mm} mm',
    wind_badge: 'Wind: {kmh} km/h',
    uv_badge: 'UV: {uv}',
    data_notes: 'Data notes',
    // card badges
    badge_total: '{v} total',
    badge_travel: '{v} travel',
    badge_walk: '{km} km walk',
    difficulty_easy: 'easy',
    difficulty_medium: 'medium',
    difficulty_hard: 'hard',
    confidence_live: 'live',
    confidence_mixed: 'mixed',
    confidence_fallback: 'fallback',
    confidence_estimated: 'estimated',
    data_word: 'data',
    bd_time_fit: 'Time Fit',
    bd_weather_fit: 'Weather Fit',
    bd_distance_fit: 'Distance Fit',
    bd_safety_fit: 'Safety Fit',
    bd_group_fit: 'Group Fit',
    bd_interest_fit: 'Interest Fit',
    bd_place_quality: 'Place Quality',
    why_title: 'Why recommended',
    risks_title: 'Risks',
    no_risk: 'No major risk detected by MVP rules.',
    unit_min: 'min',
    unit_h: 'h',
    unit_m: 'm',
    rejected_title: 'Rejected alternatives',
    rejected_subtitle: 'Shown for transparency. These options were not ranked at the top.',
    score_label: 'Score {score}/100',
    feedback_saved: 'Feedback saved.',
    feedback_error: 'Could not save feedback: {error}',
    reason_prompt: 'Why not useful?',
    reason_too_far: 'Too far',
    reason_too_difficult: 'Too difficult',
    reason_bad_weather: 'Bad weather',
    reason_not_interesting: 'Not interesting',
    reason_inaccurate: 'Inaccurate',
    reason_other: 'Other',
    search_failed: 'Search failed: {error}',
    demo_status: 'Demo location set: Tivat, Montenegro.',
    geo_unavailable: 'Geolocation is not available in this browser.',
    geo_insecure_status:
      'Geolocation is blocked because this page is not a secure context. Using demo coordinates.',
    geo_insecure_error:
      'Geolocation needs HTTPS or http://localhost. This page is {origin}. Open it via http://localhost:8080 (or behind HTTPS), then try again.',
    geo_requesting: 'Requesting location permission...',
    geo_set: 'Location set with accuracy about {accuracy} m.',
    geo_denied: 'Permission was denied. Allow location access for this site in your browser, then try again.',
    geo_position_unavailable:
      'Position is unavailable. Your device could not determine a location (no GPS/Wi-Fi signal).',
    geo_timeout: 'The location request timed out. Try again.',
    geo_fail_status: 'Could not get your location. Using demo coordinates.',
    geo_fail_error: 'Could not get location: {reason}',
  },
  ru: {
    app_title: 'AI-планировщик приключений (MVP)',
    hero_title: 'Найдите мини-приключение рядом',
    hero_subtitle:
      'Укажите время, транспорт и интересы. Приложение ранжирует места поблизости по погоде, усилиям на дорогу и соответствию.',
    use_location: 'Использовать мою геолокацию',
    use_demo: 'Демо: Тиват',
    loc_not_set: 'Локация ещё не задана.',
    trip_request: 'Параметры поездки',
    free_text: 'Свободный текст',
    request_text_default: 'Семейная поездка на 5 часов: крепость, история и виды.',
    latitude: 'Широта',
    longitude: 'Долгота',
    available_time: 'Доступное время',
    opt_30min: '30 мин',
    opt_1h: '1 час',
    opt_2h: '2 часа',
    opt_4h: '4 часа',
    opt_5h: '5 часов',
    opt_allday: 'Весь день',
    transport: 'Транспорт',
    t_walk: 'Пешком',
    t_car: 'Машина',
    t_bike: 'Велосипед',
    group: 'Группа',
    g_solo: 'Один',
    g_couple: 'Пара',
    g_family: 'Семья',
    g_dog: 'С собакой',
    intensity: 'Интенсивность',
    i_easy: 'Лёгкая',
    i_medium: 'Средняя',
    i_active: 'Активная',
    children_ages: 'Возраст детей',
    max_walking: 'Макс. пешком, км',
    context: 'Контекст',
    with_dog: 'С собакой',
    with_elderly: 'С пожилыми',
    reduced_mobility: 'Ограниченная мобильность',
    interests: 'Интересы',
    c_history: 'История',
    c_fortresses: 'Крепости',
    c_viewpoints: 'Смотровые',
    c_nature: 'Природа',
    c_water: 'Вода',
    c_food: 'Еда',
    c_surprise: 'Удиви меня',
    use_live: 'Использовать онлайн OSM / погоду / маршруты при наличии',
    find_adventure: 'Найти приключение',
    loading_title: 'Анализируем варианты',
    loading_subtitle: 'Проверяем места, погоду, время в пути и правила риска.',
    results_title: 'Рекомендации',
    history_title: 'Недавно просмотренное',
    clear_history: 'Очистить историю',
    history_opened: 'открыто',
    history_cleared: 'История очищена.',
    history_confirm: 'Удалить вашу историю (недавние поиски, отзывы и события)?',
    score_breakdown: 'Разбор оценки',
    open_maps: 'Google Карты',
    open_apple_maps: 'Apple Карты',
    useful: '👍 Полезно',
    not_useful: '👎 Не полезно',
    photo_source: 'Фото: {source}',
    place_weather_title: 'Погода в месте назначения',
    on_arrival: 'По прибытии',
    forecast_arrival: 'прибытие',
    weather_context: 'Погода в текущей точке',
    src_live: 'Онлайн',
    src_fallback: 'Резерв',
    weather_fit: 'Соответствие погоды {score}/100',
    rain_badge: 'Дождь 24ч: {mm} мм',
    wind_badge: 'Ветер: {kmh} км/ч',
    uv_badge: 'УФ: {uv}',
    data_notes: 'Заметки о данных',
    badge_total: 'всего {v}',
    badge_travel: 'в пути {v}',
    badge_walk: '{km} км пешком',
    difficulty_easy: 'лёгкий',
    difficulty_medium: 'средний',
    difficulty_hard: 'сложный',
    confidence_live: 'онлайн',
    confidence_mixed: 'смешанные',
    confidence_fallback: 'резервные',
    confidence_estimated: 'оценочные',
    data_word: 'данные',
    bd_time_fit: 'Время',
    bd_weather_fit: 'Погода',
    bd_distance_fit: 'Дорога',
    bd_safety_fit: 'Безопасность',
    bd_group_fit: 'Группа',
    bd_interest_fit: 'Интересы',
    bd_place_quality: 'Качество места',
    why_title: 'Почему рекомендуем',
    risks_title: 'Риски',
    no_risk: 'Существенных рисков по правилам MVP не выявлено.',
    unit_min: 'мин',
    unit_h: 'ч',
    unit_m: 'м',
    rejected_title: 'Отклонённые варианты',
    rejected_subtitle: 'Показаны для прозрачности. Эти варианты не попали в топ.',
    score_label: 'Оценка {score}/100',
    feedback_saved: 'Отзыв сохранён.',
    feedback_error: 'Не удалось сохранить отзыв: {error}',
    reason_prompt: 'Почему не полезно?',
    reason_too_far: 'Слишком далеко',
    reason_too_difficult: 'Слишком сложно',
    reason_bad_weather: 'Плохая погода',
    reason_not_interesting: 'Неинтересно',
    reason_inaccurate: 'Неточно',
    reason_other: 'Другое',
    search_failed: 'Поиск не удался: {error}',
    demo_status: 'Демо-локация задана: Тиват, Черногория.',
    geo_unavailable: 'Геолокация недоступна в этом браузере.',
    geo_insecure_status:
      'Геолокация заблокирована: страница не в защищённом контексте. Используются демо-координаты.',
    geo_insecure_error:
      'Для геолокации нужен HTTPS или http://localhost. Текущий адрес: {origin}. Откройте через http://localhost:8080 (или по HTTPS) и попробуйте снова.',
    geo_requesting: 'Запрашиваем разрешение на геолокацию...',
    geo_set: 'Локация определена с точностью около {accuracy} м.',
    geo_denied: 'Доступ запрещён. Разрешите доступ к геолокации для этого сайта и попробуйте снова.',
    geo_position_unavailable:
      'Местоположение недоступно. Устройство не смогло определить координаты (нет сигнала GPS/Wi-Fi).',
    geo_timeout: 'Время запроса геолокации истекло. Попробуйте снова.',
    geo_fail_status: 'Не удалось определить вашу локацию. Используются демо-координаты.',
    geo_fail_error: 'Не удалось получить локацию: {reason}',
  },
};

function detectLang() {
  const saved = localStorage.getItem('lang');
  if (saved === 'en' || saved === 'ru') return saved;
  return (navigator.language || '').toLowerCase().startsWith('ru') ? 'ru' : 'en';
}

let currentLang = detectLang();

function t(key, params) {
  const dict = I18N[currentLang] || I18N.en;
  let str = dict[key] != null ? dict[key] : I18N.en[key];
  if (str == null) return key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      str = str.replaceAll(`{${name}}`, value);
    }
  }
  return str;
}

function applyStaticI18n() {
  document.documentElement.lang = currentLang;
  document.title = t('app_title');
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('.lang-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.lang === currentLang);
  });
  applyDefaultRequestText();
}

// Replace the free-text default only while it is untouched (empty or equal to a
// known default in any language), so we never overwrite something the user typed.
function applyDefaultRequestText() {
  const el = $('requestText');
  if (!el) return;
  const knownDefaults = Object.keys(I18N).map((lang) => I18N[lang].request_text_default);
  const value = el.value.trim();
  if (value === '' || knownDefaults.includes(value)) {
    el.value = t('request_text_default');
  }
}

function setLang(lang) {
  if (lang !== 'en' && lang !== 'ru' && lang !== currentLang) return;
  currentLang = lang;
  localStorage.setItem('lang', lang);
  applyStaticI18n();
  // Re-render cached results so all UI labels switch instantly, without firing
  // another search (and another rate-limited Overpass call). Backend-generated
  // sentences (weather summary, why, warnings, rejected reasons) stay in the
  // language they were fetched in until the next search.
  if (lastResponse && !resultsEl.classList.contains('hidden')) {
    renderResults(lastResponse, { scroll: false });
  }
  loadHistory();
}

document.querySelectorAll('.lang-btn').forEach((btn) => {
  btn.addEventListener('click', () => setLang(btn.dataset.lang));
});

// ---------------------------------------------------------------------------

function setLocation(lat, lon, label) {
  $('lat').value = Number(lat).toFixed(6);
  $('lon').value = Number(lon).toFixed(6);
  $('locationStatus').textContent = label;
}

$('useLocationBtn').addEventListener('click', () => {
  if (!navigator.geolocation) {
    setError(t('geo_unavailable'));
    return;
  }
  // The Geolocation API only works in a secure context: HTTPS, or HTTP on
  // localhost / 127.0.0.1. Opening the app over plain HTTP via a LAN IP or
  // hostname makes the browser reject getCurrentPosition before it even
  // prompts. Detect that up front so the message is actionable.
  if (!window.isSecureContext) {
    $('locationStatus').textContent = t('geo_insecure_status');
    setError(t('geo_insecure_error', { origin: location.origin }));
    return;
  }
  $('locationStatus').textContent = t('geo_requesting');
  navigator.geolocation.getCurrentPosition(
    (position) => {
      setLocation(
        position.coords.latitude,
        position.coords.longitude,
        t('geo_set', { accuracy: Math.round(position.coords.accuracy) })
      );
    },
    (error) => {
      const reasons = {
        1: t('geo_denied'),
        2: t('geo_position_unavailable'),
        3: t('geo_timeout'),
      };
      const reason = reasons[error.code] || error.message;
      $('locationStatus').textContent = t('geo_fail_status');
      setError(t('geo_fail_error', { reason }));
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
});

$('demoBtn').addEventListener('click', () => {
  setLocation(42.4304, 18.6964, t('demo_status'));
});

document.querySelectorAll('.chip').forEach((button) => {
  button.addEventListener('click', () => button.classList.toggle('active'));
});

$('searchBtn').addEventListener('click', runSearch);
$('clearHistoryBtn').addEventListener('click', clearHistory);

function selectedInterests() {
  return Array.from(document.querySelectorAll('.chip.active')).map((button) => button.dataset.interest);
}

function parseChildrenAges(value) {
  return value
    .split(',')
    .map((item) => parseInt(item.trim(), 10))
    .filter((value) => Number.isFinite(value) && value >= 0 && value <= 18);
}

function requestPayload() {
  const maxWalkingValue = $('maxWalkingKm').value;
  return {
    lat: parseFloat($('lat').value),
    lon: parseFloat($('lon').value),
    available_minutes: parseInt($('availableMinutes').value, 10),
    transport_mode: $('transportMode').value,
    group_type: $('groupType').value,
    children_ages: parseChildrenAges($('childrenAges').value),
    with_dog: $('withDog').checked,
    with_elderly: $('withElderly').checked,
    reduced_mobility: $('reducedMobility').checked,
    intensity: $('intensity').value,
    interests: selectedInterests(),
    max_walking_km: maxWalkingValue === '' ? null : parseFloat(maxWalkingValue),
    request_text: $('requestText').value,
    use_live_data: $('useLiveData').checked,
    limit: 5,
    lang: currentLang,
    anonymous_id: anonymousId(),
  };
}

function setError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove('hidden');
}

function clearError() {
  errorBox.classList.add('hidden');
  errorBox.textContent = '';
}

// Lightweight, fire-and-forget analytics. Never blocks or fails the UI.
function track(event, extra = {}) {
  fetch('/api/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event, anonymous_id: anonymousId(), ...extra }),
  }).catch(() => {});
}

async function runSearch() {
  clearError();
  resultsEl.classList.add('hidden');
  loadingEl.classList.remove('hidden');
  track('search_started');

  try {
    const response = await fetch('/api/recommendations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload()),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed: ${response.status}`);
    }
    const data = await response.json();
    renderResults(data);
    track('search_completed', { request_id: data.request_id, meta: { count: (data.recommendations || []).length } });
    loadHistory();
  } catch (error) {
    setError(t('search_failed', { error: error.message }));
  } finally {
    loadingEl.classList.add('hidden');
  }
}

function minutes(value) {
  if (value < 60) return `${value} ${t('unit_min')}`;
  const h = Math.floor(value / 60);
  const m = value % 60;
  return m ? `${h}${t('unit_h')} ${m}${t('unit_m')}` : `${h}${t('unit_h')}`;
}

function renderResults(data, { scroll = true } = {}) {
  lastResponse = data;
  lastRequestId = data.request_id;
  $('requestIdBadge').textContent = data.request_id.slice(0, 8);
  renderWeather(data.weather);
  renderWarnings(data.data_warnings || []);
  renderCards(data.recommendations || []);
  renderRejected(data.rejected_alternatives || []);
  resultsEl.classList.remove('hidden');
  if (scroll) {
    resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function renderWeather(weather) {
  const source = weather.confidence === 'live' ? t('src_live') : t('src_fallback');
  $('weatherBox').innerHTML = `
    <h3>${t('weather_context')} <span class="badge">${source}</span></h3>
    <p>${escapeHtml(weather.summary)}</p>
    <div class="badges">
      <span class="badge">${t('weather_fit', { score: weather.score })}</span>
      ${weather.temperature_c != null ? `<span class="badge">${Math.round(weather.temperature_c)}°C</span>` : ''}
      ${weather.rain_mm_last_24h != null ? `<span class="badge">${t('rain_badge', { mm: weather.rain_mm_last_24h.toFixed(1) })}</span>` : ''}
      ${weather.wind_kmh != null ? `<span class="badge">${t('wind_badge', { kmh: Math.round(weather.wind_kmh) })}</span>` : ''}
      ${weather.uv_index != null ? `<span class="badge">${t('uv_badge', { uv: weather.uv_index.toFixed(1) })}</span>` : ''}
    </div>
  `;
}

function renderWarnings(warnings) {
  const container = $('dataWarnings');
  if (!warnings.length) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = `
    <div class="card">
      <h3>${t('data_notes')}</h3>
      ${warnings.map((warning) => `<div class="item warn">${escapeHtml(warning)}</div>`).join('')}
    </div>
  `;
}

function renderCards(items) {
  cardsEl.innerHTML = '';
  const template = $('recommendationTemplate');
  items.forEach((item) => {
    const node = template.content.cloneNode(true);
    node.querySelector('.title').textContent = item.title;
    node.querySelector('.description').textContent = item.description;
    renderPhoto(node, item);
    node.querySelector('.score').textContent = item.adventure_score;
    node.querySelector('.breakdown-summary').textContent = t('score_breakdown');
    const mapLink = node.querySelector('.map-link');
    mapLink.textContent = t('open_maps');
    const appleLink = node.querySelector('.apple-map-link');
    appleLink.textContent = t('open_apple_maps');
    appleLink.href = item.apple_map_url;
    mapLink.addEventListener('click', () =>
      track('maps_opened', { request_id: lastRequestId, recommendation_id: item.id, meta: { provider: 'google' } }),
    );
    appleLink.addEventListener('click', () =>
      track('maps_opened', { request_id: lastRequestId, recommendation_id: item.id, meta: { provider: 'apple' } }),
    );
    node.querySelector('.feedback-up').textContent = t('useful');
    node.querySelector('.feedback-down').textContent = t('not_useful');
    node.querySelector('.badges').innerHTML = `
      <span class="badge">${t('badge_total', { v: minutes(item.total_minutes) })}</span>
      <span class="badge">${t('badge_travel', { v: minutes(item.travel_minutes) })}</span>
      <span class="badge">${t('badge_walk', { km: item.walking_km.toFixed(1) })}</span>
      <span class="badge">${t('difficulty_' + item.difficulty)}</span>
      <span class="badge ${item.data_confidence === 'fallback' ? 'warn' : ''}">${t('confidence_' + item.data_confidence)} ${t('data_word')}</span>
    `;
    renderPlaceWeather(node, item);
    node.querySelector('.breakdown').innerHTML = Object.entries(item.score_breakdown)
      .map(([key, value]) => `<div class="item"><strong>${breakdownLabel(key)}:</strong> ${value}/100</div>`)
      .join('');
    node.querySelector('.why').innerHTML = `
      <h3>${t('why_title')}</h3>
      ${item.why.map((text) => `<div class="item good">✓ ${escapeHtml(text)}</div>`).join('')}
    `;
    node.querySelector('.warnings').innerHTML = item.warnings.length
      ? `<h3>${t('risks_title')}</h3>${item.warnings.map((text) => `<div class="item warn">⚠ ${escapeHtml(text)}</div>`).join('')}`
      : `<div class="item good">${t('no_risk')}</div>`;
    mapLink.href = item.map_url;
    const details = node.querySelector('details');
    if (details) {
      details.addEventListener('toggle', () => {
        if (details.open) track('recommendation_opened', { request_id: lastRequestId, recommendation_id: item.id });
      });
    }
    const reasonsBox = node.querySelector('.feedback-reasons');
    node.querySelector('.feedback-up').addEventListener('click', () => submitFeedback(item.id, 'up'));
    node.querySelector('.feedback-down').addEventListener('click', () => toggleReasonPicker(reasonsBox, item.id));
    cardsEl.appendChild(node);
  });
}

function renderPhoto(node, item) {
  const media = node.querySelector('.place-media');
  const img = node.querySelector('.photo');
  const credit = node.querySelector('.photo-credit');
  const photo = item.photo;

  if (!photo || !photo.url) {
    media.remove();
    return;
  }

  img.src = photo.url;
  img.alt = item.title;
  img.decoding = 'async';
  img.addEventListener('error', () => media.remove(), { once: true });

  if (photo.source) {
    const label = t('photo_source', { source: photo.source });
    if (photo.source_url && /^https?:\/\//.test(photo.source_url)) {
      const link = document.createElement('a');
      link.href = photo.source_url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = label;
      credit.appendChild(link);
    } else {
      credit.textContent = label;
    }
  }

  media.classList.remove('hidden');
}

// Per-place destination weather: a Weather-Fit badge, the conditions expected on
// arrival, and an hourly strip from now through travel into the visit. Hidden
// when no forecast was available (live data off, or the forecast call failed).
function renderPlaceWeather(node, item) {
  const container = node.querySelector('.place-weather');
  if (!container) return;
  const arrival = item.arrival_weather;
  const hours = item.forecast || [];
  if (!arrival && !hours.length) {
    container.remove();
    return;
  }

  const fitBadge =
    arrival && arrival.score != null ? `<span class="badge">${t('weather_fit', { score: arrival.score })}</span>` : '';
  let arrivalLine = '';
  if (arrival) {
    const temp = arrival.temperature_c != null ? ` · ${Math.round(arrival.temperature_c)}°C` : '';
    arrivalLine = `<p class="pw-arrival">${t('on_arrival')}: ${escapeHtml(arrival.summary)}${temp}</p>`;
  }
  const strip = hours.length ? `<div class="forecast-strip">${hours.map(forecastHour).join('')}</div>` : '';

  container.innerHTML = `
    <div class="pw-head"><h3>${t('place_weather_title')}</h3>${fitBadge}</div>
    ${arrivalLine}
    ${strip}
  `;
}

function forecastHour(hour) {
  const temp = hour.temperature_c != null ? `${Math.round(hour.temperature_c)}°` : '';
  const rain = hour.precipitation_mm > 0 ? ` 🌧${hour.precipitation_mm.toFixed(1)}` : '';
  const tag = hour.is_arrival ? `<span class="fh-tag">${t('forecast_arrival')}</span>` : '';
  return `
    <div class="forecast-hour${hour.is_arrival ? ' arrival' : ''}">
      <span class="fh-off">+${hour.hour_offset}${t('unit_h')}</span>
      <span class="fh-time">${escapeHtml(hour.time)}</span>
      <span class="fh-temp">${temp}</span>
      <span class="fh-sky">${escapeHtml(hour.label)}${rain}</span>
      ${tag}
    </div>
  `;
}

function renderRejected(items) {
  const box = $('rejectedBox');
  if (!items.length) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `
    <h3>${t('rejected_title')}</h3>
    <p>${t('rejected_subtitle')}</p>
    ${items.map((item) => `<div class="item warn"><strong>${escapeHtml(item.title)}</strong>: ${escapeHtml(item.reason)}. ${t('score_label', { score: item.score })}.</div>`).join('')}
  `;
}

// A down-vote asks why first: reveal a chip-picker of reasons and submit the
// chosen one. An up-vote submits immediately with no reason.
const FEEDBACK_REASONS = ['too_far', 'too_difficult', 'bad_weather', 'not_interesting', 'inaccurate', 'other'];

function toggleReasonPicker(box, recommendationId) {
  if (!box.classList.contains('hidden')) {
    box.classList.add('hidden');
    return;
  }
  box.innerHTML =
    `<span class="reason-label">${t('reason_prompt')}</span>` +
    FEEDBACK_REASONS.map(
      (reason) => `<button type="button" class="chip" data-reason="${reason}">${t('reason_' + reason)}</button>`,
    ).join('');
  box.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      box.classList.add('hidden');
      submitFeedback(recommendationId, 'down', chip.dataset.reason);
    });
  });
  box.classList.remove('hidden');
}

async function submitFeedback(recommendationId, rating, reason) {
  if (!lastRequestId) return;
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: lastRequestId,
        recommendation_id: recommendationId,
        rating,
        reason: reason || null,
        anonymous_id: anonymousId(),
      }),
    });
    track('feedback_submitted', {
      request_id: lastRequestId,
      recommendation_id: recommendationId,
      meta: { rating, reason: reason || null },
    });
    alert(t('feedback_saved'));
  } catch (error) {
    alert(t('feedback_error', { error: error.message }));
  }
}

// "Recently seen" — recommendations this anonymous_id has been shown, newest
// first, with an "opened" badge derived from analytics events. Plus a control
// to delete all local history (sessions, recommendations, feedback, events).
async function loadHistory() {
  try {
    const res = await fetch(`/api/history?anonymous_id=${encodeURIComponent(anonymousId())}`);
    const data = await res.json();
    renderHistory(data.items || []);
  } catch (error) {
    renderHistory([]);
  }
}

function renderHistory(items) {
  const section = $('historySection');
  const list = $('historyList');
  if (!items.length) {
    section.classList.add('hidden');
    list.innerHTML = '';
    return;
  }
  const locale = currentLang === 'ru' ? 'ru-RU' : 'en-US';
  list.innerHTML = items
    .map((item) => {
      let when = item.created_at;
      try {
        when = new Date(item.created_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
      } catch (error) {}
      const opened = item.opened ? `<span class="badge">${t('history_opened')}</span>` : '';
      return `<div class="item"><strong>${escapeHtml(item.title)}</strong> · ${t('score_label', { score: item.score })} · ${escapeHtml(when)} ${opened}</div>`;
    })
    .join('');
  section.classList.remove('hidden');
}

async function clearHistory() {
  if (!confirm(t('history_confirm'))) return;
  try {
    await fetch(`/api/history?anonymous_id=${encodeURIComponent(anonymousId())}`, { method: 'DELETE' });
  } catch (error) {}
  renderHistory([]);
  alert(t('history_cleared'));
}

function breakdownLabel(key) {
  const dict = I18N[currentLang] || I18N.en;
  return dict['bd_' + key] || I18N.en['bd_' + key] || humanize(key);
}

function humanize(key) {
  return key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

// Apply translations on initial load.
applyStaticI18n();
loadHistory();
