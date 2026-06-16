# AI Adventure Planner — MVP

A runnable, single-container B2C AI Adventure Planner: tell it where you are, how
much time you have and who you're with, and it returns 3–5 nearby trip ideas
ranked by an **Adventure Score**, each with travel time, arrival weather, a map
link and a plain-language explanation of why it fits.

The core flow:

1. Get the user's location (browser geolocation, tap-the-map, or demo coords).
2. Collect time, transport, group, intensity and interests — via a guided
   wizard or a single free-text sentence ("family trip, 5 hours, fortress and
   views") parsed by an LLM.
3. Find nearby candidate places (OpenStreetMap, optionally enriched with Google
   Places ratings + photos).
4. Analyze weather at the destination for the estimated arrival time.
5. Estimate round-trip route time.
6. Compute the Adventure Score and rank candidates.
7. Return recommendations with explanations, warnings, photos and map links —
   plus the alternatives that were rejected and why.
8. Persist sessions, feedback and analytics; learn lightweight per-user
   preferences over time.

It is intentionally one package — a FastAPI backend that also serves a
mobile-first static frontend — so a single command runs both the API and the UI
at the same origin. It can later be split into a separate frontend and backend.

The UI is plain static files in the top-level `frontend/` directory
(`index.html`, `app.js`, `styles.css`, plus a small mood layer and vendored
Leaflet/Lucide); the FastAPI backend in `backend/` serves them.

Milestone scopes are tracked in [`docs/MVP_*_SCOPE.md`](docs/). The project
started as MVP 0.1 and has iterated through the LLM explanation layer (0.2),
Google Places enrichment (0.3), free-text trip parsing (0.4) and intent-aware
search (0.5).

---

## What is included

**Recommendation engine**

- FastAPI backend, mobile-first browser UI, served same-origin
- OpenStreetMap / Overpass place search (with mirror fallback)
- Optional Google Places enrichment — real ratings blended into place quality,
  plus photo backfill and a live-candidate fallback in OSM-sparse areas
- Open-Meteo weather, with an OpenWeather adapter when `OPENWEATHER_API_KEY` is set
- Destination weather at the estimated arrival time, plus an hourly forecast timeline
- OSRM routing for car/bike/walk, with a Haversine distance fallback
- Adventure Score v0.2 (adds Personal Preference Fit from feedback history)
- Recommendation explanations, warnings and rejected alternatives
- Intent-aware search: a `drinks` category and a primary-intent re-rank that
  leads with matching places on focused single-interest searches

**LLM layer (optional, provider-agnostic over the OpenAI `/v1` chat API)**

- Model-written, fact-grounded explanations with a grounding guard that falls
  back to rule-based templates if a model invents data
- Free-text "Describe your trip" parsing into structured request fields
- A/B testing of LLM explanations vs templates, bucketed by anonymous id
- An offline eval harness for the explanation layer (see [`backend/eval/`](backend/eval/README.md))

**Product & ops**

- Bilingual UI and generated text (English / Russian)
- SQLite persistence for sessions, recommendations, feedback and analytics events
- Lightweight per-user history, "visited" tracking and a "show others" rotation
  (anonymous client id, no accounts/PII)
- Per-client-IP rate limiting (slowapi) and app-side daily budgets for the paid
  Google Places / LLM calls
- Graceful degradation: every external dependency has a fallback, and the UI
  shows data-confidence warnings (live vs fallback)
- Docker / docker compose, a Fly.io deploy config, and GitHub Actions CI

---

## What is intentionally not included

- User accounts, authentication and real PII
- Adventure Memory / diary and photo uploads
- Community Intelligence (Telegram / Reddit / Facebook signals)
- Event Impact Layer and live traffic feeds
- Push notifications and payments

These are planned for later versions. An LLM semantic re-rank of candidates by
the raw free-text wish is scoped but deferred (see
[`docs/MVP_0.5_SCOPE.md`](docs/MVP_0.5_SCOPE.md)).

---

## Run locally (Docker — recommended)

```bash
docker compose up --build
```

Open:

```text
http://localhost:8000
```

The app runs with **no API keys** — it falls back to Open-Meteo weather,
Haversine routing, sample places when needed, and rule-based explanations.
Add keys via a `.env` file (see [Environment variables](#environment-variables))
to unlock OpenWeather, Google Places and the LLM features.

On iPhone/Android in the same Wi-Fi network, use your computer's local IP:

```text
http://YOUR_LOCAL_IP:8000      # e.g. http://192.168.1.23:8000
```

Browser geolocation usually requires HTTPS, except on `localhost`. If phone
geolocation is blocked over local HTTP, use the demo coordinates, tap the map,
or enter coordinates manually — or serve over HTTPS via the tunnel below.

### Without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Serve over HTTPS (Cloudflare Quick Tunnel)

To test on a phone with working geolocation you need HTTPS. A separate entry
point, `docker-compose.tunnel.yml`, layers a `cloudflared` service on top of the
base compose file to open a free **Cloudflare Quick Tunnel** — no Cloudflare
account, token or domain required. It hands you a public
`https://<random>.trycloudflare.com` URL that proxies to the app.

Start the app together with the tunnel:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up --build
```

Then read the generated HTTPS URL from the tunnel logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml \
  logs cloudflared | grep trycloudflare.com
```

Open that URL on your phone — geolocation works because the connection is HTTPS.

Notes:

- The URL is random and changes every time the tunnel restarts.
- A plain `docker compose up` ignores `docker-compose.tunnel.yml` and runs the
  app locally only at `http://localhost:8000`; the tunnel is opt-in.

---

## Environment variables

All variables are optional — the app runs without any. Copy the template and
edit what you need:

```bash
cp .env.example .env
```

`docker compose` loads `.env` automatically. Highlights (see
[`.env.example`](.env.example) for the full, commented list):

```text
# Weather
OPENWEATHER_API_KEY=            # optional; Open-Meteo fallback used if absent
USE_OPEN_METEO_FALLBACK=true

# External services
OVERPASS_URL=https://overpass-api.de/api/interpreter
OVERPASS_MIRRORS=               # optional comma-separated fallbacks
OSRM_URL=https://router.project-osrm.org
HTTP_TIMEOUT_SECONDS=8
SQLITE_PATH=./data/adventures.db

# Security / abuse protection (slowapi, per client IP)
ALLOWED_ORIGINS=                # empty = same-origin only
RATE_LIMIT_RECOMMENDATIONS=10/minute;100/day
RATE_LIMIT_PARSE=5/minute;30/day

# Google Places enrichment (optional; backend-only key)
GOOGLE_PLACES_API_KEY=
GOOGLE_PLACES_DAILY_LIMIT=800

# LLM explanation + free-text parsing (optional)
LLM_PROVIDER=template           # template (offline) | openai | gemini | ollama | ...
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=
LLM_PARSE_ENABLED=true
```

With `LLM_PROVIDER=template` (the default) the explanation layer is fully
offline and rule-based, and the "Describe your trip" field stays hidden. Point
`LLM_PROVIDER` at an OpenAI-compatible provider (a hosted one or a local
llama.cpp / Ollama server) to enable model-written explanations and free-text
parsing.

---

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | Liveness + version |
| `GET`  | `/api/features` | Which optional features are enabled (e.g. `parse`) |
| `GET`  | `/api/sample-request` | A ready-to-POST example request body |
| `POST` | `/api/parse-request` | Parse a free-text sentence into request fields (LLM) |
| `POST` | `/api/recommendations` | The main endpoint — returns ranked recommendations |
| `POST` / `GET` | `/api/feedback` | Submit / summarize thumbs up/down feedback |
| `POST` / `GET` | `/api/events` | Submit / summarize analytics events |
| `GET`  | `/api/ab` | A/B variant summary |
| `POST` / `DELETE` | `/api/visited` | Mark a place visited / clear |
| `GET` / `DELETE` | `/api/history` | Per-user history / delete a user's data |

### Recommendations

```http
POST /api/recommendations
```

```json
{
  "lat": 42.4304,
  "lon": 18.6964,
  "available_minutes": 300,
  "transport_mode": "car",
  "group_type": "family",
  "children_ages": [6, 13],
  "intensity": "easy",
  "interests": ["history", "fortresses", "viewpoints"],
  "max_walking_km": 3,
  "request_text": "Family trip for 5 hours with fortress, history and views.",
  "lang": "en",
  "use_live_data": true,
  "anonymous_id": "abc123",
  "limit": 5
}
```

### Feedback

```http
POST /api/feedback
```

```json
{
  "request_id": "...",
  "recommendation_id": "...",
  "rating": "up",
  "reason": "not_interesting",
  "anonymous_id": "abc123"
}
```

---

## Adventure Score v0.2

The score is a weighted blend of factors computable from available data:

```text
18% Time Fit
18% Weather Fit
14% Safety Fit
13% Distance Fit
10% Personal Preference Fit   (from this user's feedback; neutral on cold start)
 9% Group Fit
 9% Interest Fit
 9% Place Quality
```

On a focused single-interest search the top-N are re-ordered so matching places
lead, without changing any card's displayed score.

Future score extensions: Traffic Fit, Event Impact, Community Confidence.

---

## Implementation notes

Live APIs can fail or rate-limit. The MVP is designed to degrade gracefully:

- If Overpass fails, mirrors are tried, then fallback/sample places are used.
- If weather APIs fail, fallback weather is used.
- If OSRM fails, route time is estimated from distance and average speed.
- If the LLM is unconfigured or returns ungrounded text, rule-based explanations
  are used instead.

The UI surfaces data-confidence warnings so the user can see whether a
recommendation used live or fallback data.

---

## Tests

Run the suite inside the same image the app ships with (no source mount, so the
image must be rebuilt to pick up changes — this is also exactly what CI runs):

```bash
docker compose build app
docker compose run --rm -e PYTHONPATH=. app pytest tests
```

The suite mocks all external services and disables the rate limiter, so no API
keys are required.

---

## Deployment

A Fly.io config ships in [`fly.toml`](fly.toml): one small Machine that scales to
zero when idle, with SQLite on a persistent volume — comfortably under €5/month.
See [`docs/DEPLOY_FLY.md`](docs/DEPLOY_FLY.md) for first-time setup, secrets and
the cost breakdown.

CI is in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Pull requests to `main`** run the backend test suite (gates the PR).
- **Pushes to `main`** run tests, then deploy to Fly.io only if they pass.

---

## Next steps

1. LLM semantic re-rank of candidates by the raw free-text wish (scoped, deferred).
2. Timing intelligence (opening hours, best-time-to-go, sunset-aware planning).
3. Replace the static `frontend/` with a framework build if it outgrows plain JS.
4. Proper database migrations.
5. Event Impact Layer and live traffic.
6. Adventure Memory and richer personalization.