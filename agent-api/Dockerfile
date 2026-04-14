FROM python:3.12-slim

WORKDIR /app

ENV AWS_DEFAULT_REGION=us-east-1
ENV PYTHONUNBUFFERED=1
ENV NODE_TLS_REJECT_UNAUTHORIZED=0

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /app/data/certs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
