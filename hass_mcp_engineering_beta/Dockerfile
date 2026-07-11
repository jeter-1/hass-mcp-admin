FROM python:3.12-slim

ARG HAMCP_BUILD_SHA=unknown
ARG HAMCP_BUILD_TIME=unknown
ENV HAMCP_BUILD_SHA=${HAMCP_BUILD_SHA} \
    HAMCP_BUILD_TIME=${HAMCP_BUILD_TIME} \
    MCP_PORT=8100 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ha_mcp_engineering ./ha_mcp_engineering

CMD ["python", "-u", "-m", "ha_mcp_engineering"]
