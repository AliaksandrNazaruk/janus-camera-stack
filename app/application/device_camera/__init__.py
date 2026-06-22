"""device_camera use-cases (Phase 2A) — FastAPI-free orchestration for the parameterized
per-(serial, sensor) camera endpoints. The route (routes/device_camera.py) stays the thin HTTP
boundary: parse, call these use-cases, map domain errors to HTTP status codes, render/return.
"""
