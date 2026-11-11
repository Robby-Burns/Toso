# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Create non-root user and persistent evidence/journal directory
RUN useradd -u 1000 -m -s /bin/bash appuser && \
    mkdir -p /app/evidence && \
    chown -R appuser:appuser /app

# Copy application configuration and source code
COPY config/ /app/config/
COPY src/ /app/src/

USER appuser

# Health check verifies configuration integrity
HEALTHCHECK --interval=60s --timeout=5s --start-period=5s --retries=3 \
    CMD python -m toso validate || exit 1

# Default command: Runs the TOSO daemon worker
CMD ["python", "-m", "toso", "run"]
