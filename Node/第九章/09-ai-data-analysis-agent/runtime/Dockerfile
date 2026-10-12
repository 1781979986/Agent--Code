FROM node:22-bookworm-slim
WORKDIR /opt/runner
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-fund --no-audit --update-notifier=false && rm -rf /root/.npm
COPY entry.mjs query-worker.js ./
USER node
ENTRYPOINT ["node", "/opt/runner/entry.mjs"]
