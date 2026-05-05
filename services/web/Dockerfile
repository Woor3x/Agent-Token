FROM node:20-alpine AS deps
WORKDIR /app
RUN npm install -g pnpm --registry https://registry.npmmirror.com
RUN pnpm config set registry https://registry.npmmirror.com
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:20-alpine AS builder
WORKDIR /app
RUN npm install -g pnpm --registry https://registry.npmmirror.com
RUN pnpm config set registry https://registry.npmmirror.com
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_IDP_URL=http://localhost:8000
ARG NEXT_PUBLIC_DOC_ASSISTANT_URL=http://localhost:8100
ARG NEXT_PUBLIC_AUDIT_URL=http://localhost:8090
ARG NEXT_PUBLIC_OIDC_CLIENT_ID=web-ui
ARG NEXT_PUBLIC_ADMIN_TOKEN=admin-secret-token

ENV NEXT_PUBLIC_IDP_URL=$NEXT_PUBLIC_IDP_URL
ENV NEXT_PUBLIC_DOC_ASSISTANT_URL=$NEXT_PUBLIC_DOC_ASSISTANT_URL
ENV NEXT_PUBLIC_AUDIT_URL=$NEXT_PUBLIC_AUDIT_URL
ENV NEXT_PUBLIC_OIDC_CLIENT_ID=$NEXT_PUBLIC_OIDC_CLIENT_ID
ENV NEXT_PUBLIC_ADMIN_TOKEN=$NEXT_PUBLIC_ADMIN_TOKEN

RUN pnpm build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN npm install -g pnpm --registry https://registry.npmmirror.com
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["pnpm", "start"]
