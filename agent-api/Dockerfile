FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV AWS_DEFAULT_REGION=us-east-1
ENV PYTHONUNBUFFERED=1
ENV NODE_TLS_REJECT_UNAUTHORIZED=0

# Install minimal system dependencies including Node.js and npm for MCP servers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get install -y npm \
    && npm config set strict-ssl false \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && npm --version

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies with trusted hosts
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create data directories
RUN mkdir -p data/workflows data/storage data/logs data/config

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
