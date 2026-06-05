# AI Adventure Planner — MVP v0.1

This is a runnable vertical-slice MVP for a B2C AI Adventure Planner.

It implements the core MVP 0.1 flow:

1. Get user location.
2. Accept time, transport, group, intensity and interests.
3. Find nearby candidate places.
4. Analyze weather.
5. Estimate route time.
6. Calculate Adventure Score.
7. Return 3-5 recommendations with explanations, warnings and map links.
8. Save basic feedback.

The app is intentionally built as a simple FastAPI + mobile-first static frontend package so it can run locally immediately. It can later be split into a Next.js frontend and FastAPI backend.

The UI is a set of plain static files in the top-level `frontend/` directory (`index.html`, `app.js`, `styles.css`); the FastAPI backend in `backend/` serves them at the same origin, so the single run command below starts both the API and the UI.

---

## What is included

- FastAPI backend
- Mobile-first browser UI
- OpenStreetMap / Overpass place search
- Open-Meteo weather fallback
- OpenWeather adapter if `OPENWEATHER_API_KEY` is provided
- OSRM routing for car routes
- Haversine fallback routing
- Adventure Score v0.1
- Recommendation explanations
- Rejected alternatives
- SQLite persistence for search sessions, recommendations and feedback
- Fallback/sample places so the app works even when live APIs are unavailable

---

## What is intentionally not included in MVP 0.1

- User accounts
- Authentication
- Personalization
- Adventure Memory / diary
- Photo uploads
- Community Intelligence
- Telegram / Reddit / Facebook scraping
- Event Impact Layer
- Live traffic feed
- Push notifications
- Payments

These are planned for later versions.

---

## Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

On iPhone/Android in the same Wi-Fi network, use your computer's local IP address:

```text
http://YOUR_LOCAL_IP:8000
```

Example:

```text
http://192.168.1.23:8000
```

Browser geolocation usually requires HTTPS, except on `localhost`. If phone geolocation is blocked over local HTTP, use the demo coordinates or enter coordinates manually.

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

Open that `https://<random>.trycloudflare.com` URL on your phone — geolocation
will work because the connection is HTTPS.

Notes:

- The URL is random and changes every time the tunnel restarts.
- A plain `docker compose up` ignores `docker-compose.tunnel.yml` and runs the
  app locally only at `http://localhost:8080`; the tunnel is opt-in.

---

## Environment variables

Copy `.env.example` if needed.

```bash
cp .env.example backend/.env
```

Supported variables:

```text
OPENWEATHER_API_KEY=optional
OVERPASS_URL=https://overpass-api.de/api/interpreter
OSRM_URL=https://router.project-osrm.org
USE_OPEN_METEO_FALLBACK=true
HTTP_TIMEOUT_SECONDS=8
SQLITE_PATH=./data/adventures.db
```

The app runs without API keys.

---

## API

### Health

```http
GET /health
```

### Sample request

```http
GET /api/sample-request
```

### Recommendations

```http
POST /api/recommendations
```

Example body:

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
  "use_live_data": true,
  "limit": 5
}
```

### Feedback

```http
POST /api/feedback
```

Example body:

```json
{
  "request_id": "...",
  "recommendation_id": "...",
  "rating": "up",
  "reason": "good fit"
}
```

---

## Adventure Score v0.1

MVP v0.1 uses the practical score that can be computed with available data:

```text
20% Time Fit
20% Weather Fit
15% Distance Fit
15% Safety Fit
10% Group Fit
10% Interest Fit
10% Place Quality
```

Future score extensions:

- Traffic Fit
- Event Impact
- Community Confidence
- Personal Preference Fit

---

## Implementation notes

Live APIs can fail or rate-limit. The MVP is designed to degrade gracefully:

- If Overpass fails, fallback/sample places are used.
- If weather APIs fail, fallback weather is used.
- If OSRM fails, route time is estimated using distance and average speed.

The UI displays data warnings so the user can see whether the recommendation used live or fallback data.

---

## Tests

```bash
cd backend
PYTHONPATH=. pytest
```

---

## Next steps

1. Add real deployment.
2. Replace the static `frontend/` with Next.js if needed.
3. Add proper database migrations.
4. Add Mapbox/Google Directions for reliable routing and traffic.
5. Add Event Impact Layer.
6. Add Adventure Memory and personalization.
