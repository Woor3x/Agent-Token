## Agent base image.
##
## docker-compose.yml builds this once (image: agent-token/agent-base) and
## reuses it for doc-assistant, data-agent, web-agent.
## IdP / Gateway / Audit-API have their own Dockerfile under infra/.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g; s|http://security.debian.org|http://mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list 2>/dev/null; true \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY agents/ ./agents/
COPY sdk/ ./sdk/
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/*.sh

# pip-install the local SDK so `import agent_token_sdk` works inside containers.
RUN pip install -e ./sdk

ARG APP_MODULE=agents.doc_assistant.main:app
ENV APP_MODULE=${APP_MODULE} \
    PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn $APP_MODULE --host 0.0.0.0 --port $PORT"]
