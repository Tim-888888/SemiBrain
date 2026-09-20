ARG NODE_IMAGE
ARG NGINX_IMAGE
FROM ${NODE_IMAGE} AS build
WORKDIR /app
RUN npm install -g pnpm@11.19.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps ./apps
COPY packages/ui ./packages/ui
RUN pnpm install --frozen-lockfile && pnpm build
FROM ${NGINX_IMAGE}
COPY --from=build /app/apps/user-web/dist /usr/share/nginx/html
COPY --from=build /app/apps/admin-web/dist /usr/share/nginx/html/admin
COPY infra/proxy/foundation.conf /etc/nginx/conf.d/default.conf
