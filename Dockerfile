# syntax=docker/dockerfile:1
#
# janus-camera-stack — production image for the L4 control plane (FastAPI
# dashboard + back-channel orchestrator). Slim Python image. NO ffmpeg, NO
# /dev/video* access — those live on the edge encoder nodes, not in the L4 plane.
#
# Build (from the repo root):
#   docker build -t janus-camera-stack:latest .
#
# Run (standalone; point it at a Janus instance):
#   docker run --rm -p 8900:8900 \
#     -e JANUS_API_URL=http://host.docker.internal:8088/janus \
#     -e CAMERA_TYPE=color_camera \
#     janus-camera-stack:latest
#   # then: curl http://localhost:8900/livez   → {"ok": true}

FROM python:3.14-slim AS base

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

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app/ /app/app/
COPY static/ /app/static/
COPY templates/ /app/templates/
COPY main.py /app/main.py
# realsense_mux.py is intentionally NOT copied: it is the hardware-free
# depth-contract reference, not deployed in L4. The image runs only main:app;
# the deployed mux lives at host_infra/roles/encoder/files/realsense-mux.py on
# the camera node.
COPY textroom_relay.py /app/textroom_relay.py

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
