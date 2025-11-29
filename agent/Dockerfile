FROM node:18

WORKDIR /app

# Copy package files
COPY package*.json ./

# Disable SSL verification and install dependencies
RUN npm config set strict-ssl false && \
    npm config set registry http://registry.npmjs.org/ && \
    npm install --loglevel=error

# Copy source code
COPY src/ ./src/

# Create data directories
RUN mkdir -p data/config/triggers logs output certs

# Set environment variable to disable SSL verification for database connections
ENV NODE_TLS_REJECT_UNAUTHORIZED=0

# Expose any ports if needed (optional)
# EXPOSE 3000

# Start both scheduler and worker
CMD ["sh", "-c", "node src/scheduler/index.js & node src/worker/index.js & wait"]
