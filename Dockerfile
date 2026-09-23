FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY tests/fixtures/contract-v1/ /build/tests/fixtures/contract-v1/
RUN npm run build

FROM frontend-build AS frontend-test
RUN npm test

FROM python:3.12-slim-bookworm AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && pip check
COPY backend/ ./backend/

FROM backend-base AS backend-test
COPY pyproject.toml ./
COPY contracts/ ./contracts/
COPY tests/ ./tests/
RUN python -m pytest tests/api tests/ai -q -p no:cacheprovider \
    && python -m backend.app.openapi --check

FROM backend-base AS runtime
ENV AML_FRONTEND_DIST=/app/frontend/dist
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/
COPY tests/fixtures/contract-v1/ ./tests/fixtures/contract-v1/
COPY docker/healthcheck.py ./docker/healthcheck.py
COPY scripts/docker_smoke.py ./scripts/docker_smoke.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "docker/healthcheck.py"]
CMD ["python", "-m", "backend", "--host", "0.0.0.0", "--port", "8000"]
