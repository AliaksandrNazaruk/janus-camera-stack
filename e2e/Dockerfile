# Playwright runner — runs e2e against a separate camera-page instance.
# Usage:
#   docker build -f e2e/Dockerfile -t janus-e2e:latest e2e/
#   docker run --rm --network=host \
#     -e E2E_BASE_URL=http://127.0.0.1:18900 \
#     -e E2E_ADMIN_TOKEN=test-admin-token-123 \
#     janus-e2e:latest

FROM mcr.microsoft.com/playwright:v1.45.0-jammy

WORKDIR /e2e

COPY package.json ./
RUN npm install

COPY playwright.config.ts ./
COPY tests/ ./tests/

# Default: run all specs. Override via docker run cmd args.
CMD ["npx", "playwright", "test"]
