# syntax=docker/dockerfile:1
# Streamlit UI. It calls agent-api server-side (AGENT_API_URL), so unlike the
# React SPA there is no nginx reverse proxy and no Node build step.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps in their own layer. --trusted-host lets pip work behind a
# TLS-inspecting corporate proxy (same as agent-api/Dockerfile); the proxy
# itself comes from the HTTP(S)_PROXY build args forwarded by compose.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt

COPY . .

ENV AGENT_API_URL=http://agent-api:8000
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
