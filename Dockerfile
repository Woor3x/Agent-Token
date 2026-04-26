## Multi-purpose Dockerfile for every service in the Agent-Token demo.
##
## Build with:
##   docker build --build-arg APP_MODULE=agents.doc_assistant.main:app -t agent-token/doc_assistant .
##
## ``docker-compose.yml`` builds the same image once and overrides
## ``APP_MODULE`` + the published port per service.

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

# Default service — overridden per container in docker-compose.yml.
ARG APP_MODULE=agents.doc_assistant.main:app
ENV APP_MODULE=${APP_MODULE} \
    PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn $APP_MODULE --host 0.0.0.0 --port $PORT"]
