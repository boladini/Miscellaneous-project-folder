# --- frontend build ---
FROM node:22.11.0-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx tsc -b && npx vite build --outDir /web/dist

# --- python runtime (API + worker share one image) ---
FROM python:3.12.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LAB_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist ./static
RUN useradd -r -u 10001 lab && chown -R lab /app
USER lab
EXPOSE 8000
# role: api (default) | worker | migrate
CMD ["sh", "-c", "alembic upgrade head && python -m lab.bootstrap_cli && exec uvicorn lab.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
