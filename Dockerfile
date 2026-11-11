# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080 \
    HOST=0.0.0.0 \
    TOSO_CONFIG_PATH=/app/config/core_config_v1.yaml \
    TOSO_EVIDENCE_DIR=/app/evidence

WORKDIR /app

# Install curl for container health check probes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python package dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Create non-root user and persistent directories
RUN useradd -u 1000 -m -s /bin/bash appuser && \
    mkdir -p /app/evidence && \
    mkdir -p /app/config && \
    mkdir -p /app/web/out && \
    chown -R appuser:appuser /app

# Copy application configuration, static web console, and source code
COPY config/ /app/config/
COPY src/ /app/src/
COPY web/out/ /app/web/out/

USER appuser

EXPOSE 8080

# Health check verifies the live web operations console endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Default command: Runs the TOSO Web Operations & Governance Console
CMD ["python", "-m", "toso", "console"]
