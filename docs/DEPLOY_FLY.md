# Deploy to Fly.io (≤ €5/month)

This deploys the single FastAPI container (API + static frontend) to one small
Fly Machine that **scales to zero when idle**, with SQLite on a persistent
volume. Config lives in [`fly.toml`](../fly.toml).

## Cost model

Verified against Fly pricing (2026):

| Item | Price | Notes |
|------|-------|-------|
| shared-cpu-1x, 512MB | $3.32–5.16/mo **if run 24/7** | Billed only while serving a request. Idle-stopped ≈ rootfs only (~$0.15). Realistic MVP compute ≈ **$0–2/mo**. |
| 1GB volume | **$0.15/mo** | Holds the SQLite DB. |
| Shared IPv4 | free | Dedicated IPv4 would be $2/mo (not needed). |
| Egress (EU) | $0.02/GB | Negligible at MVP traffic. |

**Fly total: comfortably under €5/mo.** Cold start after idle is ~1–3s on the
first request. To trade ~$1 for fewer cold-path concerns, drop `memory` to
`256mb` in `fly.toml` (Python + LLM calls are tighter there).

> The €5 target covers **Fly hosting**. Google Places is billed by Google — it
> stays free/cheap via its monthly free tier **and** the spend caps below, but
> set a Cloud Console budget so it can never surprise you (see Safety).

## One-time setup

```bash
# 1. Install + log in
curl -L https://fly.io/install.sh | sh   # or: brew install flyctl
fly auth login

# 2. Pick a globally-unique app name and set it in fly.toml (`app = "..."`),
#    then create the app from the existing config:
fly apps create <your-unique-app-name>

# 3. Create the persistent volume (matches `source` + region in fly.toml):
fly volumes create adventure_data --region fra --size 1

# 4. Set secrets (never put these in fly.toml or git). Set only what you use:
fly secrets set \
  GOOGLE_PLACES_API_KEY=... \
  OPENWEATHER_API_KEY=... \
  LLM_PROVIDER=gemini \
  LLM_MODEL=gemini-2.5-flash \
  LLM_API_KEY=...

# 5. Deploy
fly deploy
```

Open the app: `fly open` (or `https://<app>.fly.dev`). Tail logs: `fly logs`.

## Routine deploys

```bash
fly deploy            # build + release
fly status            # machine state, last release
fly secrets list      # names only (values hidden)
```

Rate limits and budgets are plain env in `fly.toml` — change them and
`fly deploy` (no secret/rebuild dance needed).

## Safety checklist

Built into this config:

- **Scale to zero** — no idle compute spend (`auto_stop_machines="stop"`, `min_machines_running=0`).
- **HTTPS forced** (`force_https=true`) — also unblocks phone geolocation.
- **Per-IP HTTP rate limits** (slowapi): global `120/min`, `/api/recommendations`
  `10/min;100/day`, `/api/parse-request` `5/min;30/day`. Client IP read from
  `Fly-Client-IP`. Tune via the `RATE_LIMIT_*` env in `fly.toml`.
- **Fly concurrency backstop** — `hard_limit=25` caps simultaneous load so heavy
  requests can't pile up and OOM the 512MB machine.
- **CORS locked to same-origin** by default (`ALLOWED_ORIGINS` empty).
- **App-side Google Places budgets** (`GOOGLE_PLACES_DAILY_LIMIT` etc.).
- **Secrets** kept out of the image/git via `fly secrets`.
- **Health check** on `/health` for clean routing/restarts.

Do these in the provider consoles (the app cannot enforce them):

- **Google Cloud Console:** restrict the API key to *Places API* + your server/
  referrer; set a **per-day quota cap** at/above `GOOGLE_PLACES_DAILY_LIMIT`; add
  a **billing budget + alert** so a leaked key can't run up a bill.
- **LLM provider:** set a spend cap / billing alert if your provider supports it.
- Optionally `fly scale count 1` stays implied by the volume (one machine).

## Verify after deploy

```bash
curl -s https://<app>.fly.dev/health        # {"status":"ok",...}
# Rate limit works (expect some 429s once over budget):
for i in $(seq 1 15); do \
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    https://<app>.fly.dev/api/recommendations \
    -H 'content-type: application/json' \
    -d '{"lat":42.43,"lon":18.69,"available_minutes":120}'; done
```
