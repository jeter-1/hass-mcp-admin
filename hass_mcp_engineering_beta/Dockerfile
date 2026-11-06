FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS dependency-builder

WORKDIR /build
COPY requirements.txt requirements.lock installer.lock build-inputs.json build_inputs.py Dockerfile ./
RUN python build_inputs.py source \
    && python -m pip --isolated download --no-cache-dir --only-binary=:all: \
       --require-hashes --index-url https://pypi.org/simple \
       --dest /installer -r installer.lock \
    && python -m pip --isolated download --no-cache-dir --only-binary=:all: \
       --require-hashes --index-url https://pypi.org/simple \
       --dest /wheels -r requirements.lock

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ARG BUILD_VERSION=unknown
ARG HAMCP_BUILD_SHA=unknown
ARG HAMCP_BUILD_TIME=unknown
ARG HAMCP_BUILD_DIRTY=unknown
ENV BUILD_VERSION=${BUILD_VERSION} \
    HAMCP_BUILD_SHA=${HAMCP_BUILD_SHA} \
    HAMCP_BUILD_TIME=${HAMCP_BUILD_TIME} \
    HAMCP_BUILD_DIRTY=${HAMCP_BUILD_DIRTY} \
    MCP_PORT=8100 \
    PYTHONDONTWRITEBYTECODE=1

LABEL org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${HAMCP_BUILD_SHA}" \
      org.opencontainers.image.created="${HAMCP_BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/jeter-1/hass-mcp-admin"
LABEL io.hass-mcp.build.dirty="${HAMCP_BUILD_DIRTY}"

WORKDIR /app
COPY requirements.txt requirements.lock installer.lock build-inputs.json build_inputs.py ./
COPY --from=dependency-builder /installer /installer
COPY --from=dependency-builder /wheels /wheels
RUN python -m pip --isolated install --no-cache-dir --no-index --find-links=/installer \
       --only-binary=:all: --require-hashes -r installer.lock \
    && python -m pip --isolated install --no-cache-dir --no-index --find-links=/wheels \
       --only-binary=:all: --require-hashes -r requirements.lock \
    && python -m pip check \
    && rm -rf /installer /wheels
COPY ha_mcp_engineering ./ha_mcp_engineering
RUN --network=none python /app/build_inputs.py smoke

CMD ["python", "-u", "-m", "ha_mcp_engineering"]
