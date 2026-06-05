# AI Adventure Planner — MVP 0.1 Implementation Plan

## 1. Objective

Build the smallest possible mobile-first web product that can:

1. Get the user's current location.
2. Understand basic trip parameters.
3. Find nearby candidate places.
4. Analyze weather and route constraints.
5. Calculate Adventure Score v0.1.
6. Return 3–5 recommendations with clear explanations.
7. Open the selected route in Google Maps or Apple Maps.
8. Collect basic feedback.

The goal is to validate the core product thesis:

> Users prefer a ready-made AI recommendation over manually searching for places in maps and reviews.

---

## 2. MVP 0.1 Product Scope

### Included

- Mobile-first web app.
- Geolocation permission.
- Time selection.
- Transport mode.
- Interests.
- Basic group type.
- OpenStreetMap / Overpass candidate search.
- Weather analysis.
- Basic routing.
- Adventure Score v0.1.
- AI-generated explanation.
- 3–5 recommendations.
- Recommendation details screen.
- Open in Maps.
- Thumbs up / thumbs down feedback.
- Basic analytics.

### Excluded

- Accounts.
- Full personalization.
- Adventure Memory.
- Photo diary.
- Saved places.
- Community Intelligence.
- Automatic Event Impact Layer.
- Telegram / Reddit / Facebook parsing.
- Push notifications.
- Social features.
- Booking.
- In-app navigation.

Important: Event Impact, live traffic, community intelligence, and adventure history are future differentiators, but they should not block MVP 0.1.

---

## 3. Recommended Tech Stack

### Frontend

Next.js mobile-first web app.

Hosting: Vercel.

### Backend

Python FastAPI.

Hosting: Railway, Render, Fly.io, or similar.

### Database

PostgreSQL + PostGIS.

Recommended: Supabase PostgreSQL.

### Cache

Redis optional.

Use only if Overpass or weather calls become slow or rate-limited.

### External APIs

- OpenStreetMap / Overpass API for places.
- OpenWeather for weather.
- OSRM or Mapbox Directions for routing.
- Wikipedia / Wikidata for enrichment, optional.
- Google Places optional, only after basic OSM flow works.

### AI

OpenAI API for explanation generation and clarification logic.

The backend should compute scores. The LLM should explain the result, not invent facts.

---

## 4. Core User Flow

1. User opens app.
2. App asks for geolocation permission.
3. User selects:
   - available time;
   - transport;
   - group type;
   - interests;
   - intensity.
4. User clicks “Find adventure”.
5. Backend searches nearby places.
6. Backend checks weather.
7. Backend calculates travel time.
8. Backend calculates Adventure Score.
9. LLM creates explanations.
10. User sees 3–5 recommendations.
11. User opens details.
12. User opens route in Google Maps / Apple Maps.
13. User gives feedback.

---

## 5. Data Model for MVP 0.1

### users

Use anonymous users in MVP 0.1.

Fields:

- id
- anonymous_id
- created_at
- locale

### search_sessions

Fields:

- id
- user_id
- lat
- lon
- available_minutes
- transport_mode
- group_type
- intensity
- interests_json
- created_at

### places

Fields:

- id
- source
- source_id
- name
- type
- lat
- lon
- tags_json
- description
- geom
- created_at
- updated_at

### recommendations

Fields:

- id
- session_id
- place_id
- adventure_score
- score_breakdown_json
- explanation_json
- warnings_json
- total_minutes
- travel_minutes
- activity_minutes
- walking_km
- map_url
- created_at

### feedback

Fields:

- id
- user_id
- recommendation_id
- rating
- reason
- created_at

---

## 6. API Endpoints

### POST /recommendations

Creates a recommendation session.

Request:

```json
{
  "lat": 42.4304,
  "lon": 18.6960,
  "available_minutes": 300,
  "transport_mode": "car",
  "group_type": "family",
  "children_ages": [6, 13],
  "intensity": "easy",
  "interests": ["fortresses", "history", "viewpoints"],
  "max_walking_km": 3
}
```

Response:

```json
{
  "session_id": "session_123",
  "recommendations": [
    {
      "id": "rec_123",
      "title": "Old Fortress Walk",
      "adventure_score": 86,
      "total_minutes": 126,
      "travel_minutes": 18,
      "walking_km": 2.1,
      "difficulty": "easy",
      "why": [
        "Fits your 5-hour limit",
        "Matches your interest in history",
        "Walking distance is below 3 km"
      ],
      "warnings": [
        "No toilet nearby"
      ],
      "map_url": "https://maps.google.com/..."
    }
  ]
}
```

### GET /recommendations/{id}

Returns details for one recommendation.

### POST /feedback

Stores feedback.

Request:

```json
{
  "recommendation_id": "rec_123",
  "rating": "positive",
  "reason": "good_match"
}
```

---

## 7. Adventure Score v0.1

MVP 0.1 score:

```text
Adventure Score v0.1 =
  20% Time Fit
+ 20% Weather Fit
+ 15% Distance Fit
+ 15% Safety Fit
+ 10% Group Fit
+ 10% Interest Fit
+ 10% Place Quality
```

Excluded from MVP 0.1 score:

- Traffic Fit.
- Event Impact.
- Community Confidence.
- Personal Preference Fit.

These should be added later after the core flow is validated.

---

## 8. Implementation Phases

## Phase 0 — Product Alignment

Duration: 1–2 days

Tasks:

- Finalize MVP 0.1 scope.
- Freeze score formula v0.1.
- Define first test location or region.
- Define 5–10 test prompts.
- Define “useful recommendation” criteria.

Success Criteria:

- Scope is clear.
- Future layers are documented but not included in MVP 0.1.

---

## Phase 1 — Foundation

Duration: 3–5 days

Tasks:

### Frontend

Create Next.js app.

Pages:

- Home
- Search
- Loading
- Results
- Recommendation Details
- Feedback

### Backend

Create FastAPI service.

Modules:

- Search Service
- Weather Service
- Routing Service
- Recommendation Engine
- LLM Service
- Feedback Service

### Database

Create PostgreSQL + PostGIS schema.

Tables:

- users
- search_sessions
- places
- recommendations
- feedback

### Infrastructure

Deploy:

- Frontend to Vercel.
- Backend to Railway / Render / Fly.io.
- Database to Supabase.

Success Criteria:

- User can open the application and submit a request.
- Backend returns a mocked recommendation.

---

## Phase 2 — Mobile Search Experience

Duration: 3–5 days

Tasks:

- Implement mobile-first UI.
- Implement geolocation permission.
- Implement search form.
- Implement chips for time, transport, group, interests, intensity.
- Implement loading state.
- Implement basic validation.

Success Criteria:

- The user can complete the search flow on iPhone and Android browsers.

---

## Phase 3 — Place Data Layer

Duration: 5–8 days

Goal:

Retrieve candidate places around the user.

Tasks:

### OpenStreetMap / Overpass Integration

Collect candidate places:

- viewpoints
- beaches
- fortresses
- castles
- parks
- hiking trails
- waterfalls
- monuments
- museums
- historical places

### Place Normalization

Convert OSM data into a unified Place model.

Fields:

- id
- name
- type
- coordinates
- tags
- estimated_duration

### Radius Search

Initial radius logic:

- 30 minutes: 1–3 km walking / 5–10 min by car.
- 1 hour: 3–5 km walking / 15 min by car.
- 2 hours: 5–15 km by car.
- 4 hours: 20–40 km by car.
- Full day: 50–100 km by car.

Success Criteria:

- System can find at least 30–50 candidate places around a user.

---

## Phase 4 — Weather Layer

Duration: 2–3 days

Tasks:

Integrate OpenWeather.

Retrieve:

- current weather
- forecast
- temperature
- wind
- rain
- UV index, if available
- humidity
- sunset

Create Weather Fit score.

Rules:

- Heavy rain reduces score.
- Extreme heat reduces score.
- Strong wind reduces score for viewpoints and cliffs.
- Good weather increases score.

Success Criteria:

- Weather affects ranking.
- Weather warnings appear in recommendations.

---

## Phase 5 — Routing Layer

Duration: 3–5 days

Tasks:

Integrate OSRM or Mapbox Directions.

Calculate:

- travel time
- travel distance
- route URL

Generate:

- Distance Fit score
- total estimated time

Success Criteria:

- Every recommendation contains travel time.
- User can open selected route in Maps.

---

## Phase 6 — Adventure Scoring Engine

Duration: 4–6 days

Tasks:

Implement scoring functions:

- Time Fit
- Weather Fit
- Distance Fit
- Safety Fit
- Group Fit
- Interest Fit
- Place Quality

Create score breakdown for every candidate.

Sort candidates by final score.

Return top 3–5.

Success Criteria:

- Top recommendations are meaningfully different.
- The same place can rank differently depending on user context.

---

## Phase 7 — AI Explanation Layer

Duration: 2–4 days

Tasks:

Integrate OpenAI API.

Input to LLM:

- user request
- user context
- top candidates
- score breakdown
- warnings
- missing data fields

Output from LLM:

- short summary
- why recommended
- risks
- rejected alternatives
- data confidence note

Rules:

- LLM must not invent real-time data.
- LLM must explain missing traffic/event/community data when relevant.
- Backend score remains source of truth.

Success Criteria:

- Every recommendation contains clear human-readable explanation.

---

## Phase 8 — Results Experience

Duration: 3–5 days

### Results Screen

Display:

- title
- image placeholder
- Adventure Score
- travel time
- total duration
- difficulty
- explanation
- warnings

### Details Screen

Display:

- score breakdown
- route information
- why recommended
- risks
- data confidence
- rejected alternatives, optional

### External Navigation

Open:

- Google Maps
- Apple Maps

Success Criteria:

- User can go from request to recommendation in less than 30 seconds.

---

## Phase 9 — Feedback and Analytics

Duration: 2–3 days

Events:

- Search Started
- Search Completed
- Recommendation Viewed
- Recommendation Opened
- Maps Opened
- Feedback Submitted

Feedback values:

- positive
- negative

Reasons:

- too far
- too difficult
- bad weather
- not interesting
- inaccurate
- other

Success Criteria:

- Product can measure recommendation quality.
- Product can calculate positive feedback rate.

---

## Phase 10 — QA and Beta Test

Duration: 3–5 days

Tasks:

- Test on iPhone Safari.
- Test on Android Chrome.
- Test poor GPS permission flow.
- Test no candidate results.
- Test bad weather scenario.
- Test family scenario.
- Test short time scenario.
- Test car vs walking scenario.

Success Criteria:

- 10–20 test users can use the app without help.
- Recommendations are generated in under 30 seconds.

---

## 9. Estimated Timeline

Solo founder estimate:

- Full-time: 4–6 weeks.
- Part-time: 6–9 weeks.

Fast prototype without production polish:

- 2–3 weeks.

---

## 10. MVP Success Metrics

Primary KPI:

- User receives a useful recommendation in under 30 seconds.

Validation KPIs:

- 70%+ positive feedback.
- 30%+ users click “Open in Maps”.
- 20%+ users submit feedback.

Qualitative validation:

- Users say the app saved them time.
- Users trust the explanation.
- Users understand why some options were rejected.

---

## 11. Key Risks

### Risk 1 — Poor place data

Mitigation:

- Start with regions where OSM data is good.
- Add Wikidata / Wikipedia enrichment.
- Add Google Places later if needed.

### Risk 2 — Recommendations feel generic

Mitigation:

- Show score breakdown.
- Show concrete reasons.
- Show rejected alternatives.

### Risk 3 — API latency

Mitigation:

- Cache OSM and weather results.
- Limit candidate count before routing.
- Route only top candidates after basic filtering.

### Risk 4 — Missing traffic/event data

Mitigation:

- Explicitly mark these as unavailable in MVP 0.1.
- Do not pretend the system knows live traffic if it does not.
- Add Event Impact Layer after MVP validation.

### Risk 5 — Overbuilding

Mitigation:

- Keep MVP 0.1 focused.
- No accounts.
- No diary.
- No social features.
- No community scraping.

---

## 12. Definition of Done for MVP 0.1

MVP 0.1 is done when:

1. User can open the app on a phone.
2. User can allow location access.
3. User can enter basic trip preferences.
4. Backend finds nearby places.
5. Backend checks weather.
6. Backend estimates travel time.
7. Backend calculates Adventure Score v0.1.
8. App shows 3–5 recommendations.
9. Each recommendation has explanation and warnings.
10. User can open route in Maps.
11. User can submit feedback.
12. Basic analytics are collected.
13. Search-to-results time is under 30 seconds for most requests.
