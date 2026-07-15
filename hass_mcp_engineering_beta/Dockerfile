FROM python:3.12-slim AS dependency-builder

WORKDIR /build
COPY requirements.txt .
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim

ARG BUILD_VERSION=unknown
ARG HAMCP_BUILD_SHA=unknown
ARG HAMCP_BUILD_TIME=unknown
ENV BUILD_VERSION=${BUILD_VERSION} \
    HAMCP_BUILD_SHA=${HAMCP_BUILD_SHA} \
    HAMCP_BUILD_TIME=${HAMCP_BUILD_TIME} \
    MCP_PORT=8100 \
    PYTHONDONTWRITEBYTECODE=1

LABEL org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${HAMCP_BUILD_SHA}" \
      org.opencontainers.image.created="${HAMCP_BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/jeter-1/hass-mcp-admin"

WORKDIR /app
COPY requirements.txt .
COPY --from=dependency-builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
COPY ha_mcp_engineering ./ha_mcp_engineering

CMD ["python", "-u", "-m", "ha_mcp_engineering"]
