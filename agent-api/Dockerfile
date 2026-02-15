FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js for MCP servers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set strict-ssl false

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies including uv
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt && \
    pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org uv

# Copy application code
COPY app/ ./app/

# Create data directories
RUN mkdir -p data/workflows data/storage data/logs

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
