FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    CHROME_BIN=/usr/bin/chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates dumb-init \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN npm config set registry https://registry.npmmirror.com \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000
COPY package*.json ./
RUN npm install --omit=dev --ignore-scripts=false \
    && npm cache clean --force
COPY server.js incoming-event.js message-id.js session-files.js sender-number.js ./

RUN mkdir -p /data/auth /data/events && chown -R node:node /app /data
USER node

EXPOSE 3000
ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "server.js"]
