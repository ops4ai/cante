# Multi-stage: build the Vite/React bundle, then serve it with nginx.
# nginx proxies /v1 to the api service (see nginx.conf), so the backoffice can
# call the API same-origin with no CORS setup.

# ── build ───────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
# package-lock.json may be absent on a fresh checkout; copy if present.
COPY package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

# ── serve ───────────────────────────────────────────────────────────────
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
