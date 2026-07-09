# syntax=docker/dockerfile:1

# Stage 1: build the React SPA (Debian slim — reliable Rollup native binaries)
FROM node:20-bookworm-slim AS build

WORKDIR /app

# Optional internal npm mirror for locked-down networks (set via compose build
# arg NPM_REGISTRY). Empty by default → the public registry is used.
ARG NPM_REGISTRY=

# Corporate SSL inspection proxy uses a self-signed cert — tell npm to trust it.
# NODE_TLS_REJECT_UNAUTHORIZED covers fetches strict-ssl doesn't (build stage
# only; the shipped image is plain nginx). Retries ride out flaky proxies.
ENV NODE_TLS_REJECT_UNAUTHORIZED=0
RUN npm config set strict-ssl false && \
    npm config set fetch-retries 5 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi

# Cache deps in their own layer — only re-runs when package files change
COPY package.json package-lock.json ./
# Remove postinstall (tries to pip-install agent-api, not applicable in Docker)
RUN npm pkg delete scripts.postinstall && \
    npm ci --no-audit --no-fund

COPY . .

# Empty build args → relative /api/v1 URLs for nginx same-origin proxy
ARG VITE_API_URL=
ARG VITE_AGENT_API_URL=
ENV VITE_API_URL=$VITE_API_URL \
    VITE_AGENT_API_URL=$VITE_AGENT_API_URL

RUN npm run build

# Stage 2: serve static assets + reverse proxy (small final image)
FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
