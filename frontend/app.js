const $ = (id) => document.getElementById(id);
const carouselEl = $('carousel');
const loadingEl = $('loading');
const errorBox = $('errorBox');
const sheetEl = $('sheet');
let lastRequestId = null;
let lastResponse = null;

function anonymousId() {
  let id = localStorage.getItem('anon_id');
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `a-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem('anon_id', id);
  }
  return id;
}

// ---------------------------------------------------------------------------
// Map — revealed full-screen when results arrive (exploring mode).
// ---------------------------------------------------------------------------
let map = null;
let originMarker = null;
let resultsLayer = null;
let markersById = {};
let activeId = null;
let scrollRaf = null;
// While we programmatically scroll a card into view, pause the scroll-spy so it
// doesn't re-select whatever card is momentarily centered during the animation.
let spyPaused = false;
let spyResumeTimer = null;

function scoreIcon(score, { active = false, top = false } = {}) {
  const cls = 'map-pin' + (active ? ' is-active' : '') + (top ? ' is-top' : '');
  return L.divIcon({ className: cls, html: `<span class="pin-score">${score}</span>`, iconSize: [38, 38], iconAnchor: [19, 19] });
}

function ensureMap() {
  if (map || typeof L === 'undefined' || !document.getElementById('map')) return;
  const lat = parseFloat($('lat').value) || 42.4304;
  const lon = parseFloat($('lon').value) || 18.6964;
  map = L.map('map', { zoomControl: false }).setView([lat, lon], 13);
  window.appMap = map;
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);
  const youIcon = L.divIcon({ className: 'you-pin', html: '<span></span>', iconSize: [18, 18], iconAnchor: [9, 9] });
  originMarker = L.marker([lat, lon], { draggable: true, icon: youIcon });
  originMarker.on('dragend', () => {
    const { lat: a, lng: b } = originMarker.getLatLng();
    setOrigin(a, b, { recenter: false });
  });
  resultsLayer = L.layerGroup().addTo(map);
}

function setOrigin(lat, lon, { recenter = false } = {}) {
  const latNum = Number(lat);
  const lonNum = Number(lon);
  $('lat').value = latNum.toFixed(6);
  $('lon').value = lonNum.toFixed(6);
  if (originMarker) {
    originMarker.setLatLng([latNum, lonNum]);
    if (map && !map.hasLayer(originMarker)) originMarker.addTo(map); // reveal pin on first set
  }
  if (map && recenter) map.setView([latNum, lonNum], Math.max(map.getZoom(), 13));
  document.dispatchEvent(new CustomEvent('origin-set', { detail: { lat: latNum, lon: lonNum } }));
}

function sheetHeight() {
  return sheetEl.offsetHeight || 210;
}

function panToWithOffset(latlng) {
  if (!map) return;
  const z = map.getZoom();
  const point = map.project(latlng, z).add([0, sheetHeight() / 2 - 30]);
  map.panTo(map.unproject(point, z), { animate: true, duration: 0.4 });
}

// Double-click a card: select the place, collapse the sheet to peek, and recenter
// the map on the place at a closer zoom (offset above the peek sheet).
function focusPlace(id, zoom = 16) {
  const marker = markersById[id];
  if (!map || !marker) return;
  setActive(id, { pan: false, scroll: false });
  sheetEl.classList.remove('open');
  // Wait for the sheet's collapse transition so sheetHeight() is the peek height
  // and the map has its post-collapse size, then recenter with the peek offset.
  setTimeout(() => {
    map.invalidateSize();
    const latlng = L.latLng(marker._latlng2);
    const point = map.project(latlng, zoom).add([0, sheetHeight() / 2 - 30]);
    map.setView(map.unproject(point, zoom), zoom, { animate: true });
  }, 360);
}

function renderResultMarkers(items, { fit = true } = {}) {
  if (!map || !resultsLayer) return;
  resultsLayer.clearLayers();
  markersById = {};
  const points = [];
  items.forEach((item, index) => {
    if (typeof item.lat !== 'number' || typeof item.lon !== 'number') return;
    const marker = L.marker([item.lat, item.lon], { icon: scoreIcon(item.adventure_score, { top: index === 0 }) }).addTo(resultsLayer);
    marker._top = index === 0;
    marker._score = item.adventure_score;
    marker._latlng2 = [item.lat, item.lon];
    marker.on('click', () => setActive(item.id, { pan: true, scroll: true }));
    markersById[item.id] = marker;
    points.push([item.lat, item.lon]);
  });
  if (fit && points.length) {
    const all = originMarker ? [[originMarker.getLatLng().lat, originMarker.getLatLng().lng], ...points] : points;
    map.fitBounds(L.latLngBounds(all), { paddingTopLeft: [40, 92], paddingBottomRight: [40, sheetHeight() + 24] });
  }
}

function setActive(id, { pan = true, scroll = false } = {}) {
  if (!id) return;
  if (id !== activeId) {
    activeId = id;
    Object.entries(markersById).forEach(([mid, marker]) => {
      marker.setIcon(scoreIcon(marker._score, { active: mid === id, top: marker._top }));
    });
    carouselEl.querySelectorAll('.recommendation').forEach((card) => {
      card.classList.toggle('is-active', card.dataset.id === id);
    });
  }
  const marker = markersById[id];
  // Only pan while the sheet is in peek; when it's open the map is hidden anyway.
  if (pan && marker && !sheetEl.classList.contains('open')) panToWithOffset(L.latLng(marker._latlng2));
  if (scroll) scrollCardIntoView(id);
}

function scrollCardIntoView(id) {
  const card = carouselEl.querySelector(`.recommendation[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  pauseSpy();
  card.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
}

function pauseSpy() {
  spyPaused = true;
  if (spyResumeTimer) clearTimeout(spyResumeTimer);
  // Fallback in case 'scrollend' never fires (e.g. the card is already centered).
  spyResumeTimer = setTimeout(() => { spyPaused = false; spyResumeTimer = null; }, 700);
}

function onCarouselScroll() {
  if (spyPaused || scrollRaf) return;
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = null;
    const center = carouselEl.scrollLeft + carouselEl.clientWidth / 2;
    let best = null;
    let bestDist = Infinity;
    carouselEl.querySelectorAll('.recommendation').forEach((card) => {
      const cardCenter = card.offsetLeft + card.offsetWidth / 2;
      const dist = Math.abs(cardCenter - center);
      if (dist < bestDist) {
        bestDist = dist;
        best = card;
      }
    });
    if (best) setActive(best.dataset.id, { pan: true, scroll: false });
  });
}
carouselEl.addEventListener('scroll', onCarouselScroll, { passive: true });
// Resume the spy as soon as the programmatic scroll settles (modern browsers);
// the timeout in pauseSpy() covers browsers without 'scrollend'.
carouselEl.addEventListener('scrollend', () => {
  spyPaused = false;
  if (spyResumeTimer) { clearTimeout(spyResumeTimer); spyResumeTimer = null; }
});

// ---------------------------------------------------------------------------
// Mode switching: planning (wizard)  <->  exploring (map + sheet)
// ---------------------------------------------------------------------------
function setMode(mode) {
  document.body.classList.toggle('planning', mode === 'planning');
  document.body.classList.toggle('exploring', mode === 'exploring');
  $('editBtn').classList.toggle('hidden', mode !== 'exploring');
  $('locateBtn').classList.toggle('hidden', mode !== 'exploring');
  $('showOthersBtn').classList.toggle('hidden', mode !== 'exploring');
}

function enterExploring() {
  setMode('exploring');
  ensureMap();
  if (originMarker && map && !map.hasLayer(originMarker)) originMarker.addTo(map);
  if (map) setTimeout(() => map.invalidateSize(), 80);
}

function enterPlanning() {
  setMode('planning');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function openSheet() {
  sheetEl.classList.add('open');
}

function toggleSheet() {
  sheetEl.classList.toggle('open');
  if (map) setTimeout(() => map.invalidateSize(), 360);
}

$('sheetHandle').addEventListener('click', toggleSheet);
$('editBtn').addEventListener('click', enterPlanning);

// ---------------------------------------------------------------------------
// Internationalization (EN / RU)
// ---------------------------------------------------------------------------
const I18N = {
  en: {
    app_title: 'AI Adventure Planner',
    brand: 'Adventure Planner',
    edit_trip: 'Edit trip',
    s1_kicker: "Let's start",
    s1_title: 'How much time have you got?',
    s1_where: 'Where are you starting from?',
    s2_kicker: 'Your crew',
    s2_title: "Who's coming along?",
    s2_how: 'How are you getting there?',
    s3_kicker: 'The fun part',
    s3_title: 'What are you in the mood for?',
    step_of: 'Step {n} of {total}',
    step_when: 'When & where',
    step_who: 'Who & how',
    step_mood: 'Your mood',
    next: 'Next',
    back: 'Back',
    enter_coords: 'Enter coordinates',
    results_title: 'Your adventures',
    found_count: '{n} found',
    pick_today: 'Top pick',
    use_location: 'Use my location',
    use_demo: 'Use Tivat demo',
    loc_not_set: 'Location is not set yet.',
    map_you_are_here: 'Your location',
    map_location_set: 'Location set from the map.',
    latitude: 'Latitude',
    longitude: 'Longitude',
    opt_30min: '30 min',
    opt_1h: '1 hour',
    opt_2h: '2 hours',
    opt_4h: '4 hours',
    opt_5h: '5 hours',
    opt_allday: 'All day',
    t_walk: 'Walk',
    t_car: 'Car',
    t_bike: 'Bike',
    g_solo: 'Solo',
    g_couple: 'Couple',
    g_family: 'Family',
    i_easy: 'Easy',
    i_medium: 'Medium',
    i_active: 'Active',
    intensity: 'Intensity',
    children_ages: 'Children ages',
    max_walking: 'Max walking km',
    with_dog: 'With a dog',
    with_elderly: 'With older adults',
    reduced_mobility: 'Reduced mobility',
    c_history: 'History',
    c_fortresses: 'Fortresses',
    c_viewpoints: 'Viewpoints',
    c_nature: 'Nature',
    c_water: 'Water',
    c_food: 'Food',
    c_surprise: 'Surprise me',
    use_live: 'Use live OSM / weather / routing',
    find_adventure: 'Find my adventure',
    loading_title: 'Scouting your adventure',
    loading_subtitle: 'Checking places, weather, travel time and risk rules.',
    history_title: 'Recently seen',
    clear_history: 'Clear my history',
    clear_visited: 'Clear visited',
    visited_confirm: 'Clear all places you marked as visited?',
    visited_cleared: 'Visited places cleared.',
    mark_visited: "I've been here",
    show_others: 'Show others',
    no_more_others: 'No new places left — showing the best matches again.',
    history_opened: 'opened',
    history_cleared: 'History cleared.',
    history_confirm: 'Delete your local history (recent searches, feedback and events)?',
    score_breakdown: 'Score breakdown',
    open_maps: 'Maps',
    open_apple_maps: 'Apple',
    useful: '👍',
    not_useful: '👎',
    photo_source: 'Photo: {source}',
    place_weather_title: 'Weather at destination',
    on_arrival: 'On arrival',
    forecast_arrival: 'arrival',
    weather_context: 'Current location weather',
    src_live: 'Live',
    src_fallback: 'Fallback',
    weather_fit: 'Weather Fit {score}/100',
    rain_badge: 'Rain 24h: {mm} mm',
    wind_badge: 'Wind: {kmh} km/h',
    uv_badge: 'UV: {uv}',
    data_notes: 'Data notes',
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
    bd_personal_preference_fit: 'Personal Fit',
    why_title: 'Why here',
    risks_title: 'Worth knowing',
    no_risk: 'No major risk detected by MVP rules.',
    unit_min: 'min',
    unit_h: 'h',
    unit_m: 'm',
    rejected_title: 'Skipped routes',
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
    geo_insecure_status: 'Geolocation is blocked because this page is not a secure context. Using demo coordinates.',
    geo_insecure_error:
      'Geolocation needs HTTPS or http://localhost. This page is {origin}. Open it via http://localhost:8080 (or behind HTTPS), then try again.',
    geo_requesting: 'Requesting location permission...',
    geo_set: 'Location set with accuracy about {accuracy} m.',
    geo_denied: 'Permission was denied. Allow location access for this site in your browser, then try again.',
    geo_position_unavailable: 'Position is unavailable. Your device could not determine a location (no GPS/Wi-Fi signal).',
    geo_timeout: 'The location request timed out. Try again.',
    geo_fail_status: 'Could not get your location. Using demo coordinates.',
    geo_fail_error: 'Could not get location: {reason}',
  },
  ru: {
    app_title: 'AI-планировщик приключений',
    brand: 'Adventure Planner',
    edit_trip: 'Изменить',
    s1_kicker: 'Начнём',
    s1_title: 'Сколько у вас времени?',
    s1_where: 'Откуда стартуете?',
    s2_kicker: 'Ваша компания',
    s2_title: 'Кто едет с вами?',
    s2_how: 'Как будете добираться?',
    s3_kicker: 'Самое интересное',
    s3_title: 'Чего хочется?',
    step_of: 'Шаг {n} из {total}',
    step_when: 'Когда и откуда',
    step_who: 'Кто и как',
    step_mood: 'Настроение',
    next: 'Далее',
    back: 'Назад',
    enter_coords: 'Ввести координаты',
    results_title: 'Ваши приключения',
    found_count: 'найдено: {n}',
    pick_today: 'Топ',
    use_location: 'Моя геолокация',
    use_demo: 'Демо: Тиват',
    loc_not_set: 'Локация ещё не задана.',
    map_you_are_here: 'Ваша локация',
    map_location_set: 'Локация задана по карте.',
    latitude: 'Широта',
    longitude: 'Долгота',
    opt_30min: '30 мин',
    opt_1h: '1 час',
    opt_2h: '2 часа',
    opt_4h: '4 часа',
    opt_5h: '5 часов',
    opt_allday: 'Весь день',
    t_walk: 'Пешком',
    t_car: 'Машина',
    t_bike: 'Велосипед',
    g_solo: 'Один',
    g_couple: 'Пара',
    g_family: 'Семья',
    i_easy: 'Лёгкая',
    i_medium: 'Средняя',
    i_active: 'Активная',
    intensity: 'Интенсивность',
    children_ages: 'Возраст детей',
    max_walking: 'Макс. пешком, км',
    with_dog: 'С собакой',
    with_elderly: 'С пожилыми',
    reduced_mobility: 'Ограниченная мобильность',
    c_history: 'История',
    c_fortresses: 'Крепости',
    c_viewpoints: 'Смотровые',
    c_nature: 'Природа',
    c_water: 'Вода',
    c_food: 'Еда',
    c_surprise: 'Удиви меня',
    use_live: 'Онлайн OSM / погода / маршруты',
    find_adventure: 'Найти приключение',
    loading_title: 'Ищем ваше приключение',
    loading_subtitle: 'Проверяем места, погоду, время в пути и правила риска.',
    history_title: 'Недавно просмотренное',
    clear_history: 'Очистить историю',
    clear_visited: 'Очистить посещённые',
    visited_confirm: 'Очистить все отмеченные посещённые места?',
    visited_cleared: 'Посещённые места очищены.',
    mark_visited: 'Я здесь был',
    show_others: 'Другие места',
    no_more_others: 'Новых мест не осталось — снова показываем лучшие варианты.',
    history_opened: 'открыто',
    history_cleared: 'История очищена.',
    history_confirm: 'Удалить вашу историю (недавние поиски, отзывы и события)?',
    score_breakdown: 'Разбор оценки',
    open_maps: 'Карты',
    open_apple_maps: 'Apple',
    useful: '👍',
    not_useful: '👎',
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
    bd_personal_preference_fit: 'Личные предпочтения',
    why_title: 'Почему сюда',
    risks_title: 'Стоит знать',
    no_risk: 'Существенных рисков по правилам MVP не выявлено.',
    unit_min: 'мин',
    unit_h: 'ч',
    unit_m: 'м',
    rejected_title: 'Пропущенные маршруты',
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
    geo_insecure_status: 'Геолокация заблокирована: страница не в защищённом контексте. Используются демо-координаты.',
    geo_insecure_error:
      'Для геолокации нужен HTTPS или http://localhost. Текущий адрес: {origin}. Откройте через http://localhost:8080 (или по HTTPS) и попробуйте снова.',
    geo_requesting: 'Запрашиваем разрешение на геолокацию...',
    geo_set: 'Локация определена с точностью около {accuracy} м.',
    geo_denied: 'Доступ запрещён. Разрешите доступ к геолокации для этого сайта и попробуйте снова.',
    geo_position_unavailable: 'Местоположение недоступно. Устройство не смогло определить координаты (нет сигнала GPS/Wi-Fi).',
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
  if (window.lucide) window.lucide.createIcons();
}

function setLang(lang) {
  if (lang !== 'en' && lang !== 'ru' && lang !== currentLang) return;
  currentLang = lang;
  localStorage.setItem('lang', lang);
  applyStaticI18n();
  updateProgress();
  if (lastResponse) renderResults(lastResponse, { refit: false });
  loadHistory();
}

document.querySelectorAll('.lang-btn').forEach((btn) => {
  btn.addEventListener('click', () => setLang(btn.dataset.lang));
});

// ---------------------------------------------------------------------------
// Wizard navigation
// ---------------------------------------------------------------------------
const TOTAL_STEPS = 3;
const STEP_NAMES = { 1: 'step_when', 2: 'step_who', 3: 'step_mood' };
let currentStep = 1;

function updateProgress() {
  $('progressFill').style.width = `${(currentStep / TOTAL_STEPS) * 100}%`;
  $('progressLabel').textContent = `${t('step_of', { n: currentStep, total: TOTAL_STEPS })} · ${t(STEP_NAMES[currentStep])}`;
}

function showStep(n) {
  currentStep = Math.max(1, Math.min(TOTAL_STEPS, n));
  document.querySelectorAll('.step').forEach((s) => s.classList.toggle('is-active', Number(s.dataset.step) === currentStep));
  updateProgress();
  $('wizard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

document.querySelectorAll('[data-next]').forEach((b) => b.addEventListener('click', () => showStep(currentStep + 1)));
document.querySelectorAll('[data-back]').forEach((b) => b.addEventListener('click', () => showStep(currentStep - 1)));

// ---------------------------------------------------------------------------
// Location
// ---------------------------------------------------------------------------
function setLocation(lat, lon, label, { recenter = true } = {}) {
  setOrigin(lat, lon, { recenter });
  $('locationStatus').textContent = label;
}

function requestGeolocation() {
  if (!navigator.geolocation) return setError(t('geo_unavailable'));
  if (!window.isSecureContext) {
    $('locationStatus').textContent = t('geo_insecure_status');
    return setError(t('geo_insecure_error', { origin: location.origin }));
  }
  $('locationStatus').textContent = t('geo_requesting');
  navigator.geolocation.getCurrentPosition(
    (position) => setLocation(position.coords.latitude, position.coords.longitude, t('geo_set', { accuracy: Math.round(position.coords.accuracy) })),
    (error) => {
      const reasons = { 1: t('geo_denied'), 2: t('geo_position_unavailable'), 3: t('geo_timeout') };
      const reason = reasons[error.code] || error.message;
      $('locationStatus').textContent = t('geo_fail_status');
      setError(t('geo_fail_error', { reason }));
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

$('useLocationBtn').addEventListener('click', requestGeolocation);
$('locateBtn').addEventListener('click', requestGeolocation);
$('demoBtn').addEventListener('click', () => setLocation(42.4304, 18.6964, t('demo_status')));

['lat', 'lon'].forEach((id) =>
  $(id).addEventListener('change', () => {
    const lat = parseFloat($('lat').value);
    const lon = parseFloat($('lon').value);
    if (Number.isFinite(lat) && Number.isFinite(lon)) setOrigin(lat, lon, { recenter: true });
  }),
);

// ---------------------------------------------------------------------------
// Tile groups
// ---------------------------------------------------------------------------
function wireSingleSelect(containerId) {
  const container = $(containerId);
  if (!container) return;
  container.querySelectorAll('.tile').forEach((tile) => {
    tile.addEventListener('click', () => {
      container.querySelectorAll('.tile').forEach((t2) => t2.classList.remove('is-active'));
      tile.classList.add('is-active');
    });
  });
}

function wireMultiSelect(containerId) {
  const container = $(containerId);
  if (!container) return;
  container.querySelectorAll('.tile').forEach((tile) => {
    tile.addEventListener('click', () => tile.classList.toggle('is-active'));
  });
}

['timeChips', 'groupChips', 'transportChips', 'intensityChips'].forEach(wireSingleSelect);
wireMultiSelect('interestChips');

function activeData(containerId, key, fallback) {
  const el = $(containerId);
  const active = el && el.querySelector('.tile.is-active');
  return active ? active.dataset[key] : fallback;
}

function selectedInterests() {
  return Array.from(document.querySelectorAll('#interestChips .tile.is-active')).map((t2) => t2.dataset.interest);
}

$('searchBtn').addEventListener('click', () => runSearch());
$('showOthersBtn').addEventListener('click', () => runSearch({ excludeSeen: true }));
$('clearHistoryBtn').addEventListener('click', clearHistory);
$('clearVisitedBtn').addEventListener('click', clearVisited);

function parseChildrenAges(value) {
  return value
    .split(',')
    .map((item) => parseInt(item.trim(), 10))
    .filter((v) => Number.isFinite(v) && v >= 0 && v <= 18);
}

function requestPayload(excludeSeen = false) {
  const maxWalkingValue = $('maxWalkingKm').value;
  return {
    lat: parseFloat($('lat').value),
    lon: parseFloat($('lon').value),
    available_minutes: parseInt(activeData('timeChips', 'minutes', '300'), 10),
    transport_mode: activeData('transportChips', 'transport', 'car'),
    group_type: activeData('groupChips', 'group', 'family'),
    children_ages: parseChildrenAges($('childrenAges').value),
    with_dog: $('withDog').checked,
    with_elderly: $('withElderly').checked,
    reduced_mobility: $('reducedMobility').checked,
    intensity: activeData('intensityChips', 'intensity', 'easy'),
    interests: selectedInterests(),
    max_walking_km: maxWalkingValue === '' ? null : parseFloat(maxWalkingValue),
    request_text: null,
    use_live_data: $('useLiveData').checked,
    limit: 5,
    lang: currentLang,
    anonymous_id: anonymousId(),
    exclude_seen: excludeSeen,
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

function track(event, extra = {}) {
  fetch('/api/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event, anonymous_id: anonymousId(), ...extra }),
  }).catch(() => {});
}

async function runSearch({ excludeSeen = false } = {}) {
  clearError();
  loadingEl.classList.remove('hidden');
  track('search_started', { meta: { exclude_seen: excludeSeen } });

  try {
    const response = await fetch('/api/recommendations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload(excludeSeen)),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed: ${response.status}`);
    }
    const data = await response.json();
    sheetEl.classList.remove('open'); // start in peek so the map stays visible
    enterExploring();
    renderResults(data);
    if (excludeSeen && !(data.recommendations || []).length) setError(t('no_more_others'));
    track('search_completed', { request_id: data.request_id, meta: { count: (data.recommendations || []).length } });
    loadHistory();
  } catch (error) {
    enterExploring();
    openSheet();
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

function renderResults(data, { refit = true } = {}) {
  lastResponse = data;
  lastRequestId = data.request_id;
  const items = data.recommendations || [];
  $('sheetCount').textContent = items.length ? t('found_count', { n: items.length }) : '';
  renderWeather(data.weather);
  renderWarnings(data.data_warnings || []);
  renderCards(items);
  renderRejected(data.rejected_alternatives || []);
  const keep = activeId;
  activeId = null;
  renderResultMarkers(items, { fit: refit });
  const target = (refit ? null : keep) || (items[0] && items[0].id);
  if (target) setActive(target, { pan: false, scroll: refit });
}

function renderWeather(weather) {
  const source = weather.confidence === 'live' ? t('src_live') : t('src_fallback');
  $('weatherBox').innerHTML = `
    <h3>${t('weather_context')}</h3>
    <p>${escapeHtml(weather.summary)}</p>
    <div class="badges">
      <span class="badge">${source}</span>
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
  carouselEl.innerHTML = '';
  items.forEach((item, index) => carouselEl.appendChild(buildCard(item, index === 0)));
}

function buildCard(item, isTop) {
  const template = $('recommendationTemplate');
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector('.recommendation');
  article.dataset.id = item.id;
  if (isTop) article.classList.add('is-top');

  fragment.querySelector('.title').textContent = item.title;
  fragment.querySelector('.description').textContent = item.summary || item.description;
  fragment.querySelector('.card-mini').textContent = `${minutes(item.total_minutes)} · ${item.walking_km.toFixed(1)} km · ${t('difficulty_' + item.difficulty)}`;
  renderPhoto(fragment, item, isTop);
  fragment.querySelector('.score-num').textContent = item.adventure_score;
  fragment.querySelector('.breakdown-summary').textContent = t('score_breakdown');

  const mapLink = fragment.querySelector('.map-link');
  mapLink.textContent = t('open_maps');
  mapLink.href = item.map_url;
  const appleLink = fragment.querySelector('.apple-map-link');
  appleLink.textContent = t('open_apple_maps');
  appleLink.href = item.apple_map_url;
  mapLink.addEventListener('click', () =>
    track('maps_opened', { request_id: lastRequestId, recommendation_id: item.id, meta: { provider: 'google' } }),
  );
  appleLink.addEventListener('click', () =>
    track('maps_opened', { request_id: lastRequestId, recommendation_id: item.id, meta: { provider: 'apple' } }),
  );

  fragment.querySelector('.feedback-up').textContent = t('useful');
  fragment.querySelector('.feedback-down').textContent = t('not_useful');
  fragment.querySelector('.badges').innerHTML = `
    <span class="badge">${t('badge_total', { v: minutes(item.total_minutes) })}</span>
    <span class="badge">${t('badge_travel', { v: minutes(item.travel_minutes) })}</span>
    <span class="badge">${t('badge_walk', { km: item.walking_km.toFixed(1) })}</span>
    <span class="badge">${t('difficulty_' + item.difficulty)}</span>
    <span class="badge ${item.data_confidence === 'fallback' ? 'warn' : ''}">${t('confidence_' + item.data_confidence)} ${t('data_word')}</span>
  `;
  renderPlaceWeather(fragment, item);
  fragment.querySelector('.breakdown').innerHTML = Object.entries(item.score_breakdown)
    .map(([key, value]) => {
      const tier = value >= 88 ? 'hi' : value < 65 ? 'lo' : '';
      return `<div class="bd-row"><span class="bd-k">${breakdownLabel(key)}</span><span class="bd-bar"><i class="${tier}" style="width:${Math.max(0, Math.min(100, value))}%"></i></span><span class="bd-v">${value}</span></div>`;
    })
    .join('');
  fragment.querySelector('.why').innerHTML = `
    <h3>${t('why_title')}</h3>
    ${item.why.map((text) => `<div class="item good">✓ ${escapeHtml(text)}</div>`).join('')}
    ${item.data_confidence_note ? `<div class="item">${escapeHtml(item.data_confidence_note)}</div>` : ''}
  `;
  fragment.querySelector('.warnings').innerHTML = item.warnings.length
    ? `<h3>${t('risks_title')}</h3>${item.warnings.map((text) => `<div class="item warn">⚠ ${escapeHtml(text)}</div>`).join('')}`
    : `<div class="item good">${t('no_risk')}</div>`;

  const details = fragment.querySelector('details.breakdown-wrap');
  if (details) {
    details.addEventListener('toggle', () => {
      if (details.open) track('recommendation_opened', { request_id: lastRequestId, recommendation_id: item.id });
    });
  }
  const reasonsBox = fragment.querySelector('.feedback-reasons');
  fragment.querySelector('.feedback-up').addEventListener('click', () => submitFeedback(item.id, 'up'));
  fragment.querySelector('.feedback-down').addEventListener('click', () => toggleReasonPicker(reasonsBox, item.id));
  const visitedBtn = fragment.querySelector('.mark-visited');
  visitedBtn.textContent = t('mark_visited');
  visitedBtn.addEventListener('click', () => markVisited(item));
  // Tap a card: highlight its pin, and (from peek) open the sheet to read details.
  article.addEventListener('click', (event) => {
    if (event.target.closest('a, button, summary, input')) return;
    openSheet();
    setActive(item.id, { pan: false, scroll: true });
  });
  // Double-tap a card: focus its place on the map and collapse the sheet.
  article.addEventListener('dblclick', (event) => {
    if (event.target.closest('a, button, summary, input')) return;
    focusPlace(item.id);
  });
  return article;
}

function renderPhoto(node, item, isTop) {
  const media = node.querySelector('.place-media');
  const img = node.querySelector('.photo');
  const credit = node.querySelector('.photo-credit');
  const flag = node.querySelector('.pick-flag');
  const photo = item.photo;

  if (flag && isTop) {
    flag.textContent = t('pick_today');
    flag.classList.remove('hidden');
  }

  if (!photo || !photo.url) {
    img.remove();
    credit.remove(); // no source -> drop the empty credit bar
    if (isTop) media.classList.remove('hidden'); // keep a plain placeholder so the flag shows
    else media.remove();
    return;
  }
  img.src = photo.url;
  img.alt = item.title;
  img.decoding = 'async';
  img.addEventListener('error', () => img.remove(), { once: true });

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
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `
    <summary>${t('rejected_title')}</summary>
    <p>${t('rejected_subtitle')}</p>
    ${items.map((item) => `<div class="item warn"><strong>${escapeHtml(item.title)}</strong>: ${escapeHtml(item.reason)}. ${t('score_label', { score: item.score })}.</div>`).join('')}
  `;
}

const FEEDBACK_REASONS = ['too_far', 'too_difficult', 'bad_weather', 'not_interesting', 'inaccurate', 'other'];

function toggleReasonPicker(box, recommendationId) {
  if (!box.classList.contains('hidden')) {
    box.classList.add('hidden');
    return;
  }
  box.innerHTML =
    `<span class="reason-label">${t('reason_prompt')}</span>` +
    FEEDBACK_REASONS.map(
      (reason) => `<button type="button" class="pill" data-reason="${reason}">${t('reason_' + reason)}</button>`,
    ).join('');
  box.querySelectorAll('.pill').forEach((chip) => {
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

async function markVisited(item) {
  // Tell the server (so it's excluded from every future search), then drop the
  // card and its pin from the current results immediately.
  try {
    await fetch('/api/visited', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anonymous_id: anonymousId(), source_id: item.source_id }),
    });
  } catch (error) {}
  if (lastResponse) {
    lastResponse.recommendations = (lastResponse.recommendations || []).filter((rec) => rec.id !== item.id);
    renderResults(lastResponse, { refit: false });
  }
}

async function clearVisited() {
  if (!confirm(t('visited_confirm'))) return;
  try {
    await fetch(`/api/visited?anonymous_id=${encodeURIComponent(anonymousId())}`, { method: 'DELETE' });
  } catch (error) {}
  alert(t('visited_cleared'));
}

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

// Initial load.
applyStaticI18n();
updateProgress();
loadHistory();
