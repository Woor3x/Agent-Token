## Agent + service base image.
##
## docker-compose.yml builds this once (image: agent-token/agent-base) and
## reuses it for feishu-mock, gateway-mock, doc-assistant, data-agent, web-agent.
## The IdP is built from its own services/idp/Dockerfile (M1).

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agents/ ./agents/
COPY sdk/ ./sdk/
COPY services/ ./services/
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/*.sh

# pip-install the local SDK so `import agent_token_sdk` works inside containers.
RUN pip install -e ./sdk

ARG APP_MODULE=agents.doc_assistant.main:app
ENV APP_MODULE=${APP_MODULE} \
    PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn $APP_MODULE --host 0.0.0.0 --port $PORT"]
