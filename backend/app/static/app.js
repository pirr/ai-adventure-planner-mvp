const $ = (id) => document.getElementById(id);
const resultsEl = $('results');
const cardsEl = $('cards');
const loadingEl = $('loading');
const errorBox = $('errorBox');
let lastRequestId = null;

function setLocation(lat, lon, label) {
  $('lat').value = Number(lat).toFixed(6);
  $('lon').value = Number(lon).toFixed(6);
  $('locationStatus').textContent = label;
}

$('useLocationBtn').addEventListener('click', () => {
  if (!navigator.geolocation) {
    setError('Geolocation is not available in this browser.');
    return;
  }
  // The Geolocation API only works in a secure context: HTTPS, or HTTP on
  // localhost / 127.0.0.1. Opening the app over plain HTTP via a LAN IP or
  // hostname makes the browser reject getCurrentPosition before it even
  // prompts. Detect that up front so the message is actionable.
  if (!window.isSecureContext) {
    $('locationStatus').textContent =
      'Geolocation is blocked because this page is not a secure context. Using demo coordinates.';
    setError(
      `Geolocation needs HTTPS or http://localhost. This page is ${location.origin}. ` +
        'Open it via http://localhost:8080 (or behind HTTPS), then try again.'
    );
    return;
  }
  $('locationStatus').textContent = 'Requesting location permission...';
  navigator.geolocation.getCurrentPosition(
    (position) => {
      setLocation(
        position.coords.latitude,
        position.coords.longitude,
        `Location set with accuracy about ${Math.round(position.coords.accuracy)} m.`
      );
    },
    (error) => {
      const reasons = {
        1: 'Permission was denied. Allow location access for this site in your browser, then try again.',
        2: 'Position is unavailable. Your device could not determine a location (no GPS/Wi-Fi signal).',
        3: 'The location request timed out. Try again.',
      };
      const reason = reasons[error.code] || error.message;
      $('locationStatus').textContent = 'Could not get your location. Using demo coordinates.';
      setError(`Could not get location: ${reason}`);
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
});

$('demoBtn').addEventListener('click', () => {
  setLocation(42.4304, 18.6964, 'Demo location set: Tivat, Montenegro.');
});

document.querySelectorAll('.chip').forEach((button) => {
  button.addEventListener('click', () => button.classList.toggle('active'));
});

$('searchBtn').addEventListener('click', runSearch);

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
    intensity: $('intensity').value,
    interests: selectedInterests(),
    max_walking_km: maxWalkingValue === '' ? null : parseFloat(maxWalkingValue),
    request_text: $('requestText').value,
    use_live_data: $('useLiveData').checked,
    limit: 5,
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

async function runSearch() {
  clearError();
  resultsEl.classList.add('hidden');
  loadingEl.classList.remove('hidden');

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
  } catch (error) {
    setError(`Search failed: ${error.message}`);
  } finally {
    loadingEl.classList.add('hidden');
  }
}

function minutes(value) {
  if (value < 60) return `${value} min`;
  const h = Math.floor(value / 60);
  const m = value % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function renderResults(data) {
  lastRequestId = data.request_id;
  $('requestIdBadge').textContent = data.request_id.slice(0, 8);
  renderWeather(data.weather);
  renderWarnings(data.data_warnings || []);
  renderCards(data.recommendations || []);
  renderRejected(data.rejected_alternatives || []);
  resultsEl.classList.remove('hidden');
  resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderWeather(weather) {
  const source = weather.confidence === 'live' ? 'Live' : 'Fallback';
  $('weatherBox').innerHTML = `
    <h3>Weather context <span class="badge">${source}</span></h3>
    <p>${weather.summary}</p>
    <div class="badges">
      <span class="badge">Weather Fit ${weather.score}/100</span>
      ${weather.temperature_c != null ? `<span class="badge">${Math.round(weather.temperature_c)}°C</span>` : ''}
      ${weather.rain_mm_last_24h != null ? `<span class="badge">Rain 24h: ${weather.rain_mm_last_24h.toFixed(1)} mm</span>` : ''}
      ${weather.wind_kmh != null ? `<span class="badge">Wind: ${Math.round(weather.wind_kmh)} km/h</span>` : ''}
      ${weather.uv_index != null ? `<span class="badge">UV: ${weather.uv_index.toFixed(1)}</span>` : ''}
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
      <h3>Data notes</h3>
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
    node.querySelector('.score').textContent = item.adventure_score;
    node.querySelector('.badges').innerHTML = `
      <span class="badge">${minutes(item.total_minutes)} total</span>
      <span class="badge">${minutes(item.travel_minutes)} travel</span>
      <span class="badge">${item.walking_km.toFixed(1)} km walk</span>
      <span class="badge">${item.difficulty}</span>
      <span class="badge ${item.data_confidence === 'fallback' ? 'warn' : ''}">${item.data_confidence} data</span>
    `;
    node.querySelector('.breakdown').innerHTML = Object.entries(item.score_breakdown)
      .map(([key, value]) => `<div class="item"><strong>${humanize(key)}:</strong> ${value}/100</div>`)
      .join('');
    node.querySelector('.why').innerHTML = `
      <h3>Why recommended</h3>
      ${item.why.map((text) => `<div class="item good">✓ ${escapeHtml(text)}</div>`).join('')}
    `;
    node.querySelector('.warnings').innerHTML = item.warnings.length
      ? `<h3>Risks</h3>${item.warnings.map((text) => `<div class="item warn">⚠ ${escapeHtml(text)}</div>`).join('')}`
      : `<div class="item good">No major risk detected by MVP rules.</div>`;
    node.querySelector('.map-link').href = item.map_url;
    node.querySelector('.feedback-up').addEventListener('click', () => submitFeedback(item.id, 'up'));
    node.querySelector('.feedback-down').addEventListener('click', () => submitFeedback(item.id, 'down'));
    cardsEl.appendChild(node);
  });
}

function renderRejected(items) {
  const box = $('rejectedBox');
  if (!items.length) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `
    <h3>Rejected alternatives</h3>
    <p>Shown for transparency. These options were not ranked at the top.</p>
    ${items.map((item) => `<div class="item warn"><strong>${escapeHtml(item.title)}</strong>: ${escapeHtml(item.reason)}. Score ${item.score}/100.</div>`).join('')}
  `;
}

async function submitFeedback(recommendationId, rating) {
  if (!lastRequestId) return;
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: lastRequestId, recommendation_id: recommendationId, rating }),
    });
    alert('Feedback saved.');
  } catch (error) {
    alert(`Could not save feedback: ${error.message}`);
  }
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
