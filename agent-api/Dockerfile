# Stage 1: Node (only if really needed)
FROM node:20-slim AS node-base

# Stage 2: Python app
FROM python:3.12-slim

WORKDIR /app

# Environment variables
ENV AWS_DEFAULT_REGION=eu-west-1 \
    PYTHONUNBUFFERED=1

# Install system dependencies + Node/npm from Debian repos (no external registry needed)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && node --version && npm --version \
    && npm config set strict-ssl false

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Copy app
COPY app/ ./app/

# Create required directories
RUN mkdir -p /app/data/certs

# Expose port
EXPOSE 8000

# Start app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
