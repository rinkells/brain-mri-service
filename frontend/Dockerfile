# Build the interface with Node, then serve it with nginx, which also forwards the API
# paths to the "api" service (see nginx.conf and docker-compose.yml).

FROM node:24.22.3-alpine3.24 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Read by Vite at build time (src/lib/config.ts): the largest file the UI lets through.
# Compose passes MAX_UPLOAD_MB of the API, so both limits stay the same.
ARG VITE_MAX_UPLOAD_MB=10
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# The image's own unprivileged user; nginx listens on 8080, so it needs no capabilities.
USER 101

EXPOSE 8080
