# syntax=docker/dockerfile:1
#
# janus_camera_page — production image for L4 (FastAPI dashboard + back-channel
# orchestrator). Slim Python image. NO ffmpeg, NO /dev/video* access — those
# live on edge encoder nodes, not in cloud L4.
#
# Build:
#   docker build -f janus_camera_page/Dockerfile -t janus-camera-page:latest .
#
# Run (standalone, local Janus):
#   docker run --rm -p 8900:8900 \
#     -e JANUS_API_URL=http://host.docker.internal:8088/janus \
#     -e CAMERA_TYPE=color_camera \
#     janus-camera-page:latest

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8900 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini && \
    rm -rf /var/lib/apt/lists/*

COPY janus_camera_page/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY janus_camera_page/app/ /app/app/
COPY janus_camera_page/static/ /app/static/
COPY janus_camera_page/templates/ /app/templates/
COPY janus_camera_page/main.py /app/main.py
# realsense_mux.py intentionally NOT copied: it's the hardware-free depth-contract
# reference fixture (SOURCE_OF_TRUTH §2), not deployed. The L4 image runs only main:app;
# the deployed mux is host_infra/roles/encoder/files/realsense-mux.py on the camera node.
COPY janus_camera_page/textroom_relay.py /app/textroom_relay.py

RUN useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /var/lib/robot && chown appuser:appuser /var/lib/robot
USER appuser

EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/livez || exit 1

# tini: PID 1 reaper. Uvicorn handles SIGTERM, but tini ensures zombie
# reaping for any child processes (none expected, defensive).
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8900", "--workers", "1"]
