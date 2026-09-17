FROM node:22-alpine AS build
WORKDIR /app
ENV NUXT_TELEMETRY_DISABLED=1

# Lockfile-driven: reproducible, and it sidesteps the npm arborist 'edgesOut'
# crash that a bare `npm install` hits on this dependency tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG NUXT_PUBLIC_API_BASE=/api
ENV NUXT_PUBLIC_API_BASE=$NUXT_PUBLIC_API_BASE
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NUXT_TELEMETRY_DISABLED=1 NITRO_PORT=3000 NITRO_HOST=0.0.0.0

# Nitro's output is self-contained; no node_modules needed at runtime.
COPY --from=build /app/.output ./.output

RUN addgroup -g 10001 app && adduser -u 10001 -G app -D app
USER app

EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
