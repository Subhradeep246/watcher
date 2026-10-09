# TheWatcher — single-service image: builds the React frontend, then runs the
# FastAPI backend which serves both the API and the static frontend.

# ---- Stage 1: build the frontend --------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Geoapify key is a BUILD-time var for Vite. Pass VITE_CARTO_KEY / VITE_GEOAPIFY_KEY as
# build args.
ARG VITE_GEOAPIFY_KEY=""
ARG VITE_CARTO_KEY=""
ENV VITE_GEOAPIFY_KEY=$VITE_GEOAPIFY_KEY VITE_CARTO_KEY=$VITE_CARTO_KEY
RUN npm run build

# ---- Stage 2: backend runtime -----------------------------------------
FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
# Drop the built SPA where FastAPI serves it from.
COPY --from=frontend /fe/dist ./static
ENV WATCHER_STATIC_DIR=/app/static
# $PORT can be set by the host; defaults to 8000 for local `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
