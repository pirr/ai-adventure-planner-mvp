# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Copy the backend package and the static frontend it serves. main.py resolves
# the frontend via parents[2], so the repo-root layout must be preserved:
#   /app/backend/app/main.py  ->  /app/frontend
COPY backend/ backend/
COPY frontend/ frontend/

# uvicorn imports `app.main` from the backend dir and the app writes its SQLite
# DB under ./data relative to here (SQLITE_PATH defaults to ./data/adventures.db).
WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
