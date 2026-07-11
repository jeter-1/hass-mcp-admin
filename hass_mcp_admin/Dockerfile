FROM python:3.12-slim

ARG HAMCP_BUILD_SHA=unknown
ARG HAMCP_BUILD_TIME=unknown
ENV HAMCP_BUILD_SHA=${HAMCP_BUILD_SHA} \
    HAMCP_BUILD_TIME=${HAMCP_BUILD_TIME}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .

CMD ["python", "-u", "server.py"]
